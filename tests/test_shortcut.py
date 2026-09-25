"""Super+V binding choice. No display."""

import unittest

from winclip.shortcut import apply_plasma_shortcuts, shortcut_mode


class ShortcutTests(unittest.TestCase):
    def test_plasma_ignores_a_gnome_schema(self) -> None:
        self.assertEqual(shortcut_mode("KDE", "wayland-0", ":0"), "plasma")
        self.assertEqual(shortcut_mode("KDE", None, ":1"), "plasma")

    def test_gnome_uses_the_extension(self) -> None:
        self.assertEqual(shortcut_mode("GNOME", "wayland-0", ":0"), "extension")
        self.assertEqual(shortcut_mode("ubuntu:GNOME", None, ":1"), "extension")

    def test_x11_outside_a_desktop_grabs(self) -> None:
        self.assertEqual(shortcut_mode("", None, ":1"), "grab")
        self.assertEqual(shortcut_mode("", "", ":1"), "grab")

    def test_unknown_wayland_stays_unbound(self) -> None:
        self.assertEqual(shortcut_mode("", "wayland-0", ":0"), "unbound")
        self.assertEqual(shortcut_mode("", None, None), "unbound")

    def test_plasma_clears_klipper_and_keeps_the_launcher(self) -> None:
        updated = apply_plasma_shortcuts(
            "[plasmashell]\n"
            "activate widget 1=Meta\\tAlt+F1,Meta\\tAlt+F1,Activate Application Launcher Widget\n"
            "show-on-mouse-pos=Meta+V,Meta+V,Show Clipboard Items at Mouse Position\n"
        )
        self.assertIn(
            "show-on-mouse-pos=none,Meta+V,Show Clipboard Items at Mouse Position",
            updated,
        )
        self.assertIn("activate widget 1=Meta\\tAlt+F1,Meta\\tAlt+F1,Activate Application Launcher Widget", updated)
        self.assertIn("[services][winclip.desktop]", updated)
        self.assertIn("_launch=Meta+V,Meta+V,Clipboard", updated)
        self.assertNotIn("\nMeta=", updated)
