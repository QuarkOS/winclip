"""Socket server, paste order, and the one History writer."""

from __future__ import annotations

import os
import socket
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from winclip.history import (
    BootId,
    ClearUnpinned,
    Delete,
    Enable,
    ForBoot,
    History,
    Pin,
    Promote,
    StorageError,
    Unpin,
    load,
    read_boot_id,
    rows,
    save,
)
from winclip.panel import Panel
from winclip.paths import history_path, socket_path, state_dir
from winclip.seat import GnomeSeat, X11Seat, chord_for, grab_super_v, interpret_parts
from winclip.shortcut import shortcut_mode
from winclip.wire import Focus, Hello, Message, Offer, Toggle, encode, feed

class ExtensionLink:
    def __init__(self) -> None:
        self._sock: socket.socket | None = None

    def attach(self, sock: socket.socket) -> None:
        self._sock = sock

    def detach(self, sock: socket.socket) -> None:
        if self._sock is sock:
            self._sock = None

    def send(self, message: Message) -> bool:
        sock = self._sock
        if sock is None:
            return False
        data = encode(message)
        view = memoryview(data)
        try:
            while view:
                sent = sock.send(view)
                if sent == 0:
                    self._sock = None
                    return False
                view = view[sent:]
        except (BlockingIOError, OSError):
            self._sock = None
            return False
        return True


class Daemon:
    def __init__(self) -> None:
        self._history = History(False, BootId(""), (), ())
        self._refuse_save = False
        self._echo: bytes | None = None
        self._wm_class = ""
        self._panel: Panel | None = None
        self._seat: X11Seat | GnomeSeat | None = None
        self._link = ExtensionLink()
        self._server: socket.socket | None = None
        self._server_channel: GLib.IOChannel | None = None
        self._clients: dict[int, socket.socket] = {}
        self._channels: dict[int, GLib.IOChannel] = {}
        self._buffers: dict[int, bytes] = {}
        self._app: Gtk.Application | None = None

    def run(self) -> None:
        self._app = Gtk.Application(
            application_id="local.winclip.Daemon",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self._app.connect("startup", self._startup)
        self._app.connect("activate", lambda _app: None)
        self._load_history()
        if not self._claim_socket():
            raise SystemExit(0)
        try:
            self._app.run(None)
        finally:
            self._release_socket()

    def handle(self, command) -> None:
        updated, effect = self._history.apply(command)
        if updated != self._history:
            self._history = updated
            if not self._refuse_save:
                save(self._history, history_path())
        self._refresh()
        if effect.publish is not None:
            self.publish_and_paste(effect.publish)

    def ingest(self, record) -> None:
        digest = self._history.stored_digest(record)
        if digest is not None and digest == self._echo:
            self._echo = None
            return
        self.handle(record)

    def toggle(self) -> None:
        assert self._panel is not None and self._seat is not None
        if self._panel.visible():
            self._panel.hide()
            return
        self._wm_class = self._seat.mark_focus()
        self._panel.show()
        x, y = self._panel.bottom_center()
        self._seat.place(x, y)

    def publish_and_paste(self, payload) -> None:
        """Publish while the panel is focused, hide, then inject the chord."""
        assert self._panel is not None and self._seat is not None
        self._echo = payload.digest()
        self._seat.publish(payload)
        self._panel.hide()
        display = Gdk.Display.get_default()
        if display is not None:
            display.flush()
        self._seat.paste(chord_for(self._wm_class))

    def _startup(self, app: Gtk.Application) -> None:
        # GTK 4.14 quits when the last window hides unless the application is held.
        app.hold()
        assert self._server is not None
        self._server_channel = _io_channel(self._server)
        GLib.io_add_watch(
            self._server_channel,
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN,
            self._accept,
        )
        self._panel = Panel(
            on_turn_on=lambda: self.handle(Enable()),
            on_activate=lambda clip_id: self.handle(Promote(clip_id)),
            on_pin=lambda clip_id, pinned: self.handle(
                Pin(clip_id) if pinned else Unpin(clip_id)
            ),
            on_delete=lambda clip_id: self.handle(Delete(clip_id)),
            on_clear=lambda: self.handle(ClearUnpinned()),
        )
        app.add_window(self._panel.window)
        if os.environ.get("WAYLAND_DISPLAY"):
            seat: X11Seat | GnomeSeat = GnomeSeat()
            seat.attach(self._link)
        else:
            seat = X11Seat()
        self._seat = seat
        self._refresh()
        seat.watch(self.ingest)
        GLib.idle_add(self._maybe_grab)

    def _load_history(self) -> None:
        try:
            history = load(history_path())
        except StorageError:
            self._history = History(False, BootId(""), (), ())
            self._refuse_save = True
            return
        self._refuse_save = False
        history, _effect = history.apply(ForBoot(read_boot_id()))
        self._history = history
        save(history, history_path())

    def _refresh(self) -> None:
        assert self._panel is not None and self._seat is not None
        self._seat.set_boot_id(self._history.boot_id)
        self._panel.set_state(self._history.enabled, rows(self._history))

    def _maybe_grab(self) -> bool:
        mode = shortcut_mode(
            os.environ.get("XDG_CURRENT_DESKTOP", ""),
            os.environ.get("WAYLAND_DISPLAY"),
            os.environ.get("DISPLAY"),
        )
        if mode == "grab":
            grab_super_v(self.toggle)
        return False

    def _claim_socket(self) -> bool:
        path = socket_path()
        if _socket_is_live(path):
            return False
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if path.parent == state_dir():
            path.parent.chmod(0o700)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(8)
        server.setblocking(False)
        self._server = server
        return True

    def _release_socket(self) -> None:
        if self._server is None:
            return
        try:
            self._server.close()
        except OSError:
            pass
        self._server = None
        try:
            socket_path().unlink()
        except OSError:
            pass

    def _accept(self, _channel: GLib.IOChannel, _cond: GLib.IOCondition) -> bool:
        assert self._server is not None
        try:
            conn, _addr = self._server.accept()
        except OSError:
            return True
        conn.setblocking(False)
        channel = _io_channel(conn)
        fd = conn.fileno()
        self._clients[fd] = conn
        self._channels[fd] = channel
        self._buffers[fd] = b""
        GLib.io_add_watch(
            channel,
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_HUP,
            self._on_client,
        )
        return True

    def _on_client(self, channel: GLib.IOChannel, cond: GLib.IOCondition) -> bool:
        fd = channel.unix_get_fd()
        conn = self._clients.get(fd)
        if conn is None:
            return False
        if (cond & GLib.IO_HUP) and not (cond & GLib.IO_IN):
            self._drop(fd)
            return False
        try:
            chunk = conn.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            self._drop(fd)
            return False
        if not chunk:
            self._drop(fd)
            return False
        try:
            messages, rest = feed(self._buffers.get(fd, b"") + chunk)
        except ValueError:
            self._drop(fd)
            return False
        self._buffers[fd] = rest
        for message in messages:
            if not self._route(conn, message):
                self._drop(fd)
                return False
        return True

    def _route(self, conn: socket.socket, message: Message) -> bool:
        if isinstance(message, Hello):
            self._link.attach(conn)
            return True
        if isinstance(message, Toggle):
            self.toggle()
            return True
        if isinstance(message, Offer):
            print(
                "offer " + " ".join(f"{name}={len(data)}" for name, data in message.parts),
                file=sys.stderr,
            )
            self.ingest(interpret_parts(message.parts, self._history.boot_id))
            return True
        if isinstance(message, Focus):
            assert self._seat is not None
            self._seat.complete_focus(message.wm_class)
            return True
        return False

    def _drop(self, fd: int) -> None:
        conn = self._clients.pop(fd, None)
        self._channels.pop(fd, None)
        self._buffers.pop(fd, None)
        if conn is None:
            return
        self._link.detach(conn)
        try:
            conn.close()
        except OSError:
            pass


def _io_channel(sock: socket.socket) -> GLib.IOChannel:
    # The channel object must stay referenced. A bare fileno watch is collected
    # and the source disappears before the first client connects.
    channel = GLib.IOChannel.unix_new(sock.fileno())
    channel.set_close_on_unref(False)
    channel.set_encoding(None)
    channel.set_flags(channel.get_flags() | GLib.IOFlags.NONBLOCK)
    return channel


def _socket_is_live(path) -> bool:
    if not path.exists():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        probe.connect(str(path))
    except ConnectionRefusedError:
        path.unlink()
        return False
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        return False
    else:
        return True
    finally:
        probe.close()


def main() -> None:
    Daemon().run()
