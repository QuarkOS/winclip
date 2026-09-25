"""Where Super+V is bound. No GTK import."""

from __future__ import annotations

KLIPPER_ACTION = "show-on-mouse-pos"
KLIPPER_GROUPS = ("plasmashell", "org.kde.klipper.desktop", "klipper")
KLIPPER_CLEARED = "none,Meta+V,Show Clipboard Items at Mouse Position"
WINCLIP_DESKTOP = "winclip.desktop"
WINCLIP_LAUNCH = "Meta+V,Meta+V,Clipboard"


def shortcut_mode(desktop: str, wayland: str | None, display: str | None) -> str:
    """Pick the running session. A GNOME schema on a Plasma machine is not a GNOME session."""
    kind = _desktop_kind(desktop)
    if kind:
        return kind
    if wayland:
        return "unbound"
    if display:
        return "grab"
    return "unbound"


def apply_plasma_shortcuts(text: str) -> str:
    """Clear Klipper's Meta+V action and point the WinClip launch shortcut at Meta+V."""
    groups = _parse_config(text)
    for name in KLIPPER_GROUPS:
        current = groups.get(name, {}).get(KLIPPER_ACTION)
        groups.setdefault(name, {})[KLIPPER_ACTION] = (
            _clear_active(current) if current else KLIPPER_CLEARED
        )
    service = f"services][{WINCLIP_DESKTOP}"
    groups.setdefault(service, {})["_launch"] = WINCLIP_LAUNCH
    return _format_config(groups)


def _desktop_kind(desktop: str) -> str:
    names = {
        part.strip().upper()
        for chunk in (desktop or "").split(";")
        for part in chunk.split(":")
        if part.strip()
    }
    if "KDE" in names:
        return "plasma"
    if "GNOME" in names:
        return "extension"
    return ""


def _clear_active(value: str) -> str:
    active, separator, rest = value.partition(",")
    if not separator:
        return KLIPPER_CLEARED
    return "none," + rest


def _parse_config(text: str) -> dict[str, dict[str, str]]:
    groups: dict[str, dict[str, str]] = {}
    order: list[str] = []
    current = ""
    groups[current] = {}
    order.append(current)
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            if current not in groups:
                groups[current] = {}
                order.append(current)
            continue
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        groups.setdefault(current, {})[key.strip()] = value.strip()
    groups["__order__"] = {name: "" for name in order}
    return groups


def _format_config(groups: dict[str, dict[str, str]]) -> str:
    order = list(groups.pop("__order__", {}))
    for name in groups:
        if name not in order:
            order.append(name)
    chunks: list[str] = []
    for name in order:
        keys = groups.get(name, {})
        if not name and not keys:
            continue
        if name:
            chunks.append(f"[{name}]")
        for key, value in keys.items():
            chunks.append(f"{key}={value}")
        chunks.append("")
    return "\n".join(chunks).rstrip() + "\n"
