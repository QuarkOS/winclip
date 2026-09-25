"""Where Super+V is bound. No GTK import."""

from __future__ import annotations


def shortcut_mode(
    schema_installed: bool,
    display: str | None,
    wayland: str | None,
    gnome: bool = False,
) -> str:
    """gsettings when media-keys exists. GNOME never grabs Super. X11 grab is only outside GNOME."""
    if schema_installed:
        return "gsettings"
    if gnome or wayland:
        return "extension"
    if display:
        return "grab"
    return "unbound"
