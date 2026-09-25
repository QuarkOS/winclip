"""GTK 4 clipboard panel. Callbacks only; no boot id, MIME, or path."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from winclip.history import ClipId, Row

WIDTH = 360


def _later(callback, *args) -> None:
    GLib.idle_add(lambda: callback(*args) or False)


def _pop_then(menu: Gtk.Popover, callback, *args) -> None:
    menu.popdown()
    callback(*args)


CSS = """
window.winclip {
  background-color: transparent;
}
.panel {
  border-radius: 8px;
  padding: 12px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.28);
}
.panel.light {
  background-color: #F3F3F3;
  color: #1A1A1A;
}
.panel.dark {
  background-color: #202020;
  color: #F3F3F3;
}
.panel .title {
  font-size: 15px;
  font-weight: 600;
}
.panel .row-text {
  font-size: 13px;
}
.panel.light .secondary {
  color: #616161;
}
.panel.dark .secondary {
  color: #C6C6C6;
}
.card {
  border-radius: 8px;
  padding: 8px;
}
.panel.light .card {
  background-color: #FFFFFF;
  border: 1px solid #E5E5E5;
}
.panel.dark .card {
  background-color: #2D2D2D;
  border: 1px solid #3A3A3A;
}
.panel.light .card.selected {
  border: 2px solid #005FB8;
}
.panel.dark .card.selected {
  border: 2px solid #60CDFF;
}
.panel.light .pin.pinned {
  color: #005FB8;
}
.panel.dark .pin.pinned {
  color: #60CDFF;
}
button.flat {
  background: transparent;
  box-shadow: none;
  border: none;
}
.thumb {
  margin: 0;
}
"""


class Panel:
    def __init__(
        self,
        on_turn_on: Callable[[], None],
        on_activate: Callable[[ClipId], None],
        on_pin: Callable[[ClipId, bool], None],
        on_delete: Callable[[ClipId], None],
        on_clear: Callable[[], None],
    ) -> None:
        self._on_turn_on = on_turn_on
        self._on_activate = on_activate
        self._on_pin = on_pin
        self._on_delete = on_delete
        self._on_clear = on_clear
        self._selected: ClipId | None = None
        self._row_ids: list[ClipId] = []
        self._enabled = False

        self.window = Gtk.ApplicationWindow(title="Clipboard")
        self.window.add_css_class("winclip")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_default_size(WIDTH, -1)
        self.window.set_hide_on_close(True)
        self.window.connect("close-request", self._on_close)

        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._root.add_css_class("panel")
        self._root.add_css_class("light")
        self._root.set_size_request(WIDTH, -1)
        self.window.set_child(self._root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("header")
        title = Gtk.Label(label="Clipboard")
        title.add_css_class("title")
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        self._clear = Gtk.Button(label="Clear all")
        self._clear.add_css_class("flat")
        self._clear.connect("clicked", lambda *_args: self._on_clear())
        self._clear.set_visible(False)
        header.append(title)
        header.append(self._clear)

        self._stack = Gtk.Stack()
        off = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        off.set_margin_top(12)
        off.set_margin_bottom(12)
        off_label = Gtk.Label(label="Clipboard history is off")
        off_label.set_justify(Gtk.Justification.CENTER)
        self._turn_on = Gtk.Button(label="Turn on")
        self._turn_on.set_halign(Gtk.Align.CENTER)
        self._turn_on.connect("clicked", lambda *_args: self._on_turn_on())
        off.append(off_label)
        off.append(self._turn_on)

        empty = Gtk.Label(label="Copy text or an image to see it here.")
        empty.add_css_class("secondary")
        empty.set_wrap(True)
        empty.set_margin_top(12)
        empty.set_margin_bottom(12)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_max_content_height(420)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_child(self._list)

        self._stack.add_named(off, "off")
        self._stack.add_named(empty, "empty")
        self._stack.add_named(scrolled, "list")
        self._root.append(header)
        self._root.append(self._stack)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.window.add_controller(keys)
        self._install_css()
        self._bind_color_scheme()

    def set_state(self, enabled: bool, rows: tuple[Row, ...]) -> None:
        self._enabled = enabled
        ids = [row.id for row in rows]
        if self._selected not in ids:
            self._selected = ids[0] if ids else None
        self._clear.set_visible(enabled)
        self._clear.set_sensitive(any(not row.pinned for row in rows))
        if not enabled:
            self._stack.set_visible_child_name("off")
            return
        if not rows:
            self._row_ids = []
            self._stack.set_visible_child_name("empty")
            return
        self._rebuild(rows)
        self._stack.set_visible_child_name("list")

    def show(self) -> None:
        self.window.present()
        if self._enabled and self._row_ids:
            self._list.grab_focus()
        else:
            self._turn_on.grab_focus()

    def hide(self) -> None:
        self.window.hide()

    def visible(self) -> bool:
        return bool(self.window.get_visible())

    def bottom_center(self) -> tuple[int, int]:
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        if monitors.get_n_items() < 1:
            return (0, 0)
        geo = monitors.get_item(0).get_geometry()
        height = self.window.get_allocated_height()
        if height <= 1:
            _minimum, natural, _baseline_min, _baseline_nat = self.window.measure(
                Gtk.Orientation.VERTICAL, WIDTH
            )
            height = natural or 160
        x = geo.x + (geo.width - WIDTH) // 2
        y = geo.y + geo.height - height - 8
        return (x, y)

    def _rebuild(self, rows: tuple[Row, ...]) -> None:
        child = self._list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt
        self._row_ids = []
        for row in rows:
            self._list.append(self._card(row))
            self._row_ids.append(row.id)

    def _card(self, row: Row) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.add_css_class("card")
        if row.id == self._selected:
            card.add_css_class("selected")
        content = Gtk.Button()
        content.add_css_class("flat")
        content.set_hexpand(True)
        content.set_child(self._preview(row))
        content.connect(
            "clicked",
            lambda *_args, item_id=row.id: _later(self._on_activate, item_id),
        )
        pin = Gtk.Button()
        pin.add_css_class("flat")
        pin.add_css_class("pin")
        if row.pinned:
            pin.add_css_class("pinned")
        pin.set_icon_name("view-pin-symbolic")
        pin.set_tooltip_text("Unpin" if row.pinned else "Pin")
        pin.connect(
            "clicked",
            lambda *_args, item_id=row.id, pinned=not row.pinned: _later(
                self._on_pin, item_id, pinned
            ),
        )
        more = Gtk.MenuButton()
        more.add_css_class("flat")
        more.set_icon_name("view-more-symbolic")
        more.set_popover(self._menu(row))
        card.append(content)
        card.append(pin)
        card.append(more)
        return card

    def _preview(self, row: Row) -> Gtk.Widget:
        if row.text is not None:
            label = Gtk.Label(label=row.text)
            label.add_css_class("row-text")
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_lines(4)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_xalign(0)
            label.set_hexpand(True)
            return label
        if row.png:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(row.png))
            except GLib.Error:
                texture = None
            if texture is not None:
                picture = Gtk.Picture()
                picture.add_css_class("thumb")
                picture.set_paintable(texture)
                picture.set_can_shrink(True)
                picture.set_content_fit(Gtk.ContentFit.CONTAIN)
                picture.set_size_request(-1, min(120, texture.get_height()))
                return picture
        missing = Gtk.Label(label="Image")
        missing.add_css_class("secondary")
        missing.set_xalign(0)
        return missing

    def _menu(self, row: Row) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pin_label = "Unpin" if row.pinned else "Pin"
        pin = Gtk.Button(label=pin_label)
        pin.connect(
            "clicked",
            lambda *_args, item_id=row.id, pinned=not row.pinned, menu=popover: _later(
                _pop_then, menu, self._on_pin, item_id, pinned
            ),
        )
        delete = Gtk.Button(label="Delete")
        delete.connect(
            "clicked",
            lambda *_args, item_id=row.id, menu=popover: _later(
                _pop_then, menu, self._on_delete, item_id
            ),
        )
        box.append(pin)
        box.append(delete)
        popover.set_child(box)
        return popover

    def _on_key(self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        name = self._stack.get_visible_child_name()
        if name != "list":
            return False
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Left):
            self._move(-1)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Right):
            self._move(1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self._selected is not None:
                _later(self._on_activate, self._selected)
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and self._selected is not None:
            _later(self._on_delete, self._selected)
            return True
        return False

    def _move(self, delta: int) -> None:
        if not self._row_ids or self._selected is None:
            return
        index = self._row_ids.index(self._selected)
        nxt = index + delta
        if nxt < 0 or nxt >= len(self._row_ids):
            return
        self._selected = self._row_ids[nxt]
        child = self._list.get_first_child()
        position = 0
        while child is not None:
            if position < len(self._row_ids) and self._row_ids[position] == self._selected:
                child.add_css_class("selected")
            else:
                child.remove_css_class("selected")
            child = child.get_next_sibling()
            position += 1

    def _on_close(self, _window: Gtk.Window) -> bool:
        self.hide()
        return True

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        display = self.window.get_display() or Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _bind_color_scheme(self) -> None:
        try:
            source = Gio.SettingsSchemaSource.get_default()
            schema = (
                source.lookup("org.gnome.desktop.interface", True) if source is not None else None
            )
            if schema is None or not schema.has_key("color-scheme"):
                self._set_scheme("light")
                return
            settings = Gio.Settings.new("org.gnome.desktop.interface")
        except GLib.Error:
            self._set_scheme("light")
            return
        self._color_settings = settings

        def update(*_args: object) -> None:
            try:
                value = settings.get_string("color-scheme")
            except GLib.Error:
                value = "default"
            self._set_scheme("dark" if value == "prefer-dark" else "light")

        settings.connect("changed::color-scheme", update)
        update()

    def _set_scheme(self, name: str) -> None:
        self._root.remove_css_class("light")
        self._root.remove_css_class("dark")
        self._root.add_css_class(name)
