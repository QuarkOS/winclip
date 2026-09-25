"""Where Super+V is bound. No GTK import."""

from __future__ import annotations


def shortcut_mode(schema_installed: bool, display: str | None, wayland: str | None) -> str:
    """gsettings when media-keys exists. Otherwise the design's grab on X11, or the shell on Wayland."""
    if schema_installed:
        return "gsettings"
    if wayland:
        return "extension"
    if display:
        return "grab"
    return "unbound"
