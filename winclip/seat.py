"""Clipboard seats. X11 watches GTK. Wayland waits for extension offers."""

from __future__ import annotations

import ctypes
import struct
import subprocess
from collections.abc import Callable, Sequence
from typing import Protocol

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from winclip.history import PNG_MAGIC, SENSITIVE_MIMES, BootId, Payload, Record
from winclip.wire import MarkFocus, Message, Paste, Place

TEXT_MIMES = (
    "text/plain;charset=utf-8",
    "text/plain",
    "UTF8_STRING",
    "STRING",
)
HTML_MIMES = ("text/html",)
IMAGE_MIMES = ("image/png", "image/jpeg", "image/bmp", "image/webp")
READ_MIMES = TEXT_MIMES + HTML_MIMES + IMAGE_MIMES + tuple(sorted(SENSITIVE_MIMES))

TERMINALS = frozenset({
    "gnome-terminal-server",
    "kgx",
    "tilix",
    "kitty",
    "alacritty",
    "terminator",
    "xterm",
    "konsole",
    "foot",
    "wezterm",
    "org.wezfurlong.wezterm",
    "st",
    "xfce4-terminal",
    "mate-terminal",
    "lxterminal",
    "guake",
    "io.elementary.terminal",
})

_publishing = False
_display: ctypes.c_void_p | None = None
_error_handler_ref: ctypes._CFuncPtr | None = None

_MOD4 = 64
_LOCK = 2
_MOD2 = 16
_GRAB_ASYNC = 1
_KEY_PRESS = 2
_KEY_RELEASE = 3
_EVENT_SIZE = 192
_REVERT_TO_PARENT = 2


class ExtensionLink(Protocol):
    def send(self, message: Message) -> bool:
        """Write one frame. Return False when the extension is gone."""


class Seat(Protocol):
    def watch(self, ingest: Callable[[Record], None]) -> None:
        """Start delivering records."""

    def mark_focus(self) -> str:
        """Snapshot the focused window before the panel is shown."""

    def place(self, x: int, y: int) -> None:
        """Move the mapped Clipboard window to x, y."""

    def paste(self, chord: str) -> None:
        """Inject chord into the window mark_focus stored."""

    def publish(self, payload: Payload) -> None:
        """Offer payload on the clipboard while the panel is focused."""

    def set_boot_id(self, boot_id: BootId) -> None:
        """Stamp records with the history boot id."""

    def complete_focus(self, wm_class: str) -> None:
        """Finish a Wayland mark_focus wait."""


def chord_for(wm_class: str) -> str:
    """Return ctrl-shift-v for a terminal class, otherwise ctrl-v."""
    if not wm_class:
        return "ctrl-v"
    folded = wm_class.casefold()
    tail = folded.rsplit(".", 1)[-1]
    if folded in TERMINALS or tail in TERMINALS:
        return "ctrl-shift-v"
    return "ctrl-v"


def png_bytes(mime: str, data: bytes) -> bytes | None:
    """Return PNG bytes. Does not open a display."""
    if data.startswith(PNG_MAGIC):
        return data
    if mime not in ("image/jpeg", "image/bmp", "image/webp"):
        return None
    try:
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        loader = GdkPixbuf.PixbufLoader.new_with_mime_type(mime)
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return None
        ok, buf = pixbuf.save_to_bufferv("png", [], [])
        if not ok or not bytes(buf).startswith(PNG_MAGIC):
            return None
        return bytes(buf)
    except (GLib.Error, OSError, RuntimeError, ValueError):
        return None


def interpret_parts(
    parts: Sequence[tuple[str, bytes]],
    boot_id: BootId,
) -> Record:
    """Build a Record from seat bytes. History policy stays in history.py."""
    offered = frozenset(name for name, _data in parts)
    by_mime: dict[str, bytes] = {}
    for name, data in parts:
        by_mime.setdefault(name, data)
    text = _first_text(by_mime, TEXT_MIMES)
    html = _first_text(by_mime, HTML_MIMES)
    if text is None and html is not None:
        text = _strip_tags(html)
    png = None
    for mime in IMAGE_MIMES:
        data = by_mime.get(mime)
        if not data:
            continue
        png = png_bytes(mime, data)
        if png is not None:
            break
    return Record(offered, text, html, png, boot_id)


def publish(payload: Payload) -> None:
    """Set the GTK clipboard. The publishing flag hides one synchronous echo."""
    global _publishing
    display = Gdk.Display.get_default()
    if display is None:
        return
    providers: list[Gdk.ContentProvider] = []
    if payload.text is not None:
        raw = GLib.Bytes.new(payload.text.encode("utf-8"))
        for mime in ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING"):
            providers.append(Gdk.ContentProvider.new_for_bytes(mime, raw))
    if payload.html is not None:
        raw = GLib.Bytes.new(payload.html.encode("utf-8"))
        providers.append(Gdk.ContentProvider.new_for_bytes("text/html", raw))
    if payload.png is not None:
        raw = GLib.Bytes.new(payload.png)
        providers.append(Gdk.ContentProvider.new_for_bytes("image/png", raw))
    if not providers:
        return
    _publishing = True
    try:
        display.get_clipboard().set_content(Gdk.ContentProvider.new_union(providers))
        display.flush()
    finally:
        _publishing = False


def grab_super_v(on_press: Callable[[], None]) -> None:
    """Grab Super+V. Lock and Mod2 stay in the mask so Caps and Num Lock match."""
    display = _x_display()
    if display is None:
        return
    keysym = _lib().XStringToKeysym(b"v")
    if not keysym:
        return
    keycode = _lib().XKeysymToKeycode(display, keysym)
    if not keycode:
        return
    root = _lib().XDefaultRootWindow(display)
    for mask in (_MOD4, _MOD4 | _LOCK, _MOD4 | _MOD2, _MOD4 | _LOCK | _MOD2):
        _lib().XGrabKey(display, keycode, mask, root, False, _GRAB_ASYNC, _GRAB_ASYNC)
    _lib().XFlush(display)
    state = {"keycode": int(keycode), "on_press": on_press}
    # Poll instead of watching the X fd. An IOChannel on that fd makes XPending block
    # the GTK main loop, so the daemon never accepts clients.
    GLib.timeout_add(30, lambda: _drain_keys(state) or True)


class X11Seat:
    """CLIPBOARD selection only. PRIMARY is ignored."""

    def __init__(self) -> None:
        self._ingest: Callable[[Record], None] | None = None
        self._boot_id = BootId("")
        self._window: int | None = None
        self._generation = 0

    def set_boot_id(self, boot_id: BootId) -> None:
        self._boot_id = boot_id

    def complete_focus(self, wm_class: str) -> None:
        return

    def watch(self, ingest: Callable[[Record], None]) -> None:
        self._ingest = ingest
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.connect("changed", self._changed)

    def mark_focus(self) -> str:
        # 0 is None and 1 is PointerRoot. Neither is a window we can paste into.
        self._window = _input_focus()
        if self._window is None:
            return ""
        return _window_class(self._window)

    def place(self, x: int, y: int) -> None:
        self._move(x, y, 5)

    def paste(self, chord: str) -> None:
        if self._window is not None:
            _focus_window(self._window)
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", _xdotool_key(chord)],
            check=False,
            timeout=2,
        )

    def publish(self, payload: Payload) -> None:
        publish(payload)

    def _changed(self, clipboard: Gdk.Clipboard) -> None:
        if _publishing or self._ingest is None:
            return
        formats = clipboard.get_formats()
        if formats is None:
            return
        available = [mime for mime in READ_MIMES if formats.contain_mime_type(mime)]
        if not available:
            return
        self._generation += 1
        self._read(clipboard, available, [], self._generation)

    def _read(
        self,
        clipboard: Gdk.Clipboard,
        mimes: list[str],
        acc: list[tuple[str, bytes]],
        generation: int,
    ) -> None:
        if generation != self._generation or self._ingest is None:
            return
        if not mimes:
            parts = tuple(acc)
            print(
                "clipboard " + " ".join(f"{name}={len(data)}" for name, data in parts),
                file=_stderr(),
            )
            self._ingest(interpret_parts(parts, self._boot_id))
            return
        mime = mimes[0]
        rest = mimes[1:]

        def done(cb: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
            # The stream's bytes arrive on this main loop. A blocking read deadlocks it.
            _read_stream_async(cb, result, lambda data: self._after_mime(cb, rest, acc, generation, mime, data))

        clipboard.read_async([mime], GLib.PRIORITY_DEFAULT, None, done)

    def _after_mime(
        self,
        clipboard: Gdk.Clipboard,
        mimes: list[str],
        acc: list[tuple[str, bytes]],
        generation: int,
        mime: str,
        data: bytes,
    ) -> None:
        acc.append((mime, data))
        self._read(clipboard, mimes, acc, generation)

    def _move(self, x: int, y: int, left: int) -> bool:
        subprocess.run(
            [
                "xdotool",
                "search",
                "--name",
                "^Clipboard$",
                "windowmove",
                str(x),
                str(y),
            ],
            check=False,
            capture_output=True,
        )
        if left <= 1:
            return False
        GLib.timeout_add(20, lambda: self._move(x, y, left - 1))
        return False


class GnomeSeat:
    """Wayland seat. Watch is fed by daemon Offer routing, not by Gdk."""

    def __init__(self) -> None:
        self._ingest: Callable[[Record], None] | None = None
        self._link: ExtensionLink | None = None
        self._boot_id = BootId("")
        self._focus_loop: GLib.MainLoop | None = None
        self._focus_wm = ""

    def attach(self, link: ExtensionLink) -> None:
        self._link = link

    def set_boot_id(self, boot_id: BootId) -> None:
        self._boot_id = boot_id

    def watch(self, ingest: Callable[[Record], None]) -> None:
        self._ingest = ingest

    def mark_focus(self) -> str:
        self._focus_wm = ""
        if self._link is None or not self._link.send(MarkFocus()):
            return ""
        loop = GLib.MainLoop()
        self._focus_loop = loop
        GLib.timeout_add(200, self._quit_focus)
        loop.run()
        self._focus_loop = None
        return self._focus_wm

    def complete_focus(self, wm_class: str) -> None:
        self._focus_wm = wm_class
        self._quit_focus()

    def place(self, x: int, y: int) -> None:
        if self._link is not None:
            self._link.send(Place(x, y))

    def paste(self, chord: str) -> None:
        if self._link is not None:
            self._link.send(Paste(chord))

    def publish(self, payload: Payload) -> None:
        publish(payload)

    def _quit_focus(self) -> bool:
        if self._focus_loop is not None and self._focus_loop.is_running():
            self._focus_loop.quit()
        return False


def _first_text(by_mime: dict[str, bytes], mimes: tuple[str, ...]) -> str | None:
    for mime in mimes:
        data = by_mime.get(mime)
        if not data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if text != "":
            return text
    return None


def _strip_tags(html: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(html):
        if html[index] == "<":
            end = html.find(">", index + 1)
            if end < 0:
                out.append(html[index:])
                break
            index = end + 1
        else:
            out.append(html[index])
            index += 1
    return "".join(out)


def _read_stream_async(
    clipboard: Gdk.Clipboard,
    result: Gio.AsyncResult,
    callback: Callable[[bytes], None],
) -> None:
    try:
        stream, _mime = clipboard.read_finish(result)
    except GLib.Error:
        callback(b"")
        return
    if stream is None:
        callback(b"")
        return
    chunks: list[bytes] = []

    def step(src: Gio.InputStream, res: Gio.AsyncResult) -> None:
        try:
            piece = src.read_bytes_finish(res)
        except GLib.Error:
            callback(b"")
            return
        if piece.get_size() == 0:
            callback(b"".join(chunks))
            return
        data = piece.get_data()
        chunks.append(b"" if data is None else bytes(data))
        src.read_bytes_async(1 << 16, GLib.PRIORITY_DEFAULT, None, step)

    stream.read_bytes_async(1 << 16, GLib.PRIORITY_DEFAULT, None, step)


def _xdotool_key(chord: str) -> str:
    if chord == "ctrl-shift-v":
        return "ctrl+shift+v"
    return "ctrl+v"


def _window_class(window: int) -> str:
    try:
        completed = subprocess.run(
            ["xdotool", "getwindowclassname", str(window)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip()


def _stderr():
    import sys

    return sys.stderr


def _lib() -> ctypes.CDLL:
    if not hasattr(_lib, "cached"):
        lib = ctypes.CDLL("libX11.so.6")
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XGetInputFocus.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.XGetInputFocus.restype = ctypes.c_int
        lib.XSetInputFocus.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        lib.XSetInputFocus.restype = ctypes.c_int
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XFlush.restype = ctypes.c_int
        lib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        lib.XStringToKeysym.restype = ctypes.c_ulong
        lib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.XKeysymToKeycode.restype = ctypes.c_ubyte
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XGrabKey.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.XGrabKey.restype = ctypes.c_int
        lib.XPending.argtypes = [ctypes.c_void_p]
        lib.XPending.restype = ctypes.c_int
        lib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.XNextEvent.restype = ctypes.c_int
        lib.XPeekEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.XPeekEvent.restype = ctypes.c_int
        lib.XConnectionNumber.argtypes = [ctypes.c_void_p]
        lib.XConnectionNumber.restype = ctypes.c_int
        lib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        lib.XSetErrorHandler.restype = ctypes.c_void_p
        _lib.cached = lib
    return _lib.cached


def _x_display() -> ctypes.c_void_p | None:
    global _display
    if _display is not None:
        return _display
    opened = _lib().XOpenDisplay(None)
    if not opened:
        return None
    _display = opened
    _install_error_handler()
    return _display


def _install_error_handler() -> None:
    global _error_handler_ref
    if _error_handler_ref is not None:
        return

    def _ignore(_display: ctypes.c_void_p, _error: ctypes.c_void_p) -> int:
        return 0

    _error_handler_ref = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(
        _ignore
    )
    _lib().XSetErrorHandler(_error_handler_ref)


def _input_focus() -> int | None:
    display = _x_display()
    if display is None:
        return None
    window = ctypes.c_ulong()
    revert = ctypes.c_int()
    _lib().XGetInputFocus(display, ctypes.byref(window), ctypes.byref(revert))
    wid = int(window.value)
    if wid in (0, 1):
        return None
    return wid


def _focus_window(window: int) -> None:
    display = _x_display()
    if display is None:
        return
    _lib().XSetInputFocus(display, window, _REVERT_TO_PARENT, 0)
    _lib().XFlush(display)


def _event() -> ctypes.Array:
    return ctypes.create_string_buffer(_EVENT_SIZE)


def _event_fields(buf: ctypes.Array) -> tuple[int, int, int, int]:
    kind = struct.unpack_from("i", buf, 0)[0]
    when = struct.unpack_from("Q", buf, 56)[0]
    state = struct.unpack_from("I", buf, 80)[0]
    keycode = struct.unpack_from("I", buf, 84)[0]
    return kind, when, state, keycode


def _fire_grab(state: dict) -> bool:
    state["queued"] = False
    state["on_press"]()
    return False


def _drain_keys(state: dict) -> None:
    display = _x_display()
    if display is None:
        return
    while _lib().XPending(display):
        buf = _event()
        _lib().XNextEvent(display, buf)
        kind, when, mods, keycode = _event_fields(buf)
        if kind == _KEY_RELEASE and keycode == state["keycode"] and _lib().XPending(display):
            peek = _event()
            _lib().XPeekEvent(display, peek)
            peek_kind, peek_when, _peek_mods, peek_code = _event_fields(peek)
            if peek_kind == _KEY_PRESS and peek_code == keycode and peek_when == when:
                _lib().XNextEvent(display, peek)
                continue
        if kind == _KEY_PRESS and keycode == state["keycode"] and (mods & _MOD4):
            if not state.get("queued"):
                state["queued"] = True
                GLib.idle_add(_fire_grab, state)
