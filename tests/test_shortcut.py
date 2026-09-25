"""Super+V binding choice. No display."""

import unittest

from winclip.shortcut import shortcut_mode


class ShortcutTests(unittest.TestCase):
    def test_schema_uses_gsettings(self) -> None:
        self.assertEqual(shortcut_mode(True, ":0", "wayland-0"), "gsettings")
        self.assertEqual(shortcut_mode(True, ":1", None), "gsettings")

    def test_missing_schema_on_wayland_uses_the_extension(self) -> None:
        self.assertEqual(shortcut_mode(False, ":0", "wayland-0"), "extension")

    def test_gnome_never_grabs_super(self) -> None:
        self.assertEqual(shortcut_mode(False, ":0", None, gnome=True), "extension")
        self.assertEqual(shortcut_mode(False, ":1", "", gnome=True), "extension")

    def test_missing_schema_on_x11_grabs(self) -> None:
        self.assertEqual(shortcut_mode(False, ":1", None), "grab")
        self.assertEqual(shortcut_mode(False, ":1", ""), "grab")

    def test_missing_schema_without_a_session_stays_unbound(self) -> None:
        self.assertEqual(shortcut_mode(False, None, None), "unbound")
        self.assertEqual(shortcut_mode(False, "", ""), "unbound")
