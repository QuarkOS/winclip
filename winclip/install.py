"""Install the user service, launcher, desktop file, and optional GNOME pieces."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from winclip.shortcut import WINCLIP_DESKTOP, apply_plasma_shortcuts, shortcut_mode

UNIT = """\
[Unit]
Description=WinClip clipboard history
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=exec
ExecStart=%h/.local/bin/winclip run
Restart=on-failure

[Install]
WantedBy=default.target
"""

def main() -> None:
    if not _gtk_imports():
        _print_packages()
        raise SystemExit(1)
    installed: list[str] = []
    _copy_app()
    installed.append(str(Path.home() / ".local" / "share" / "winclip" / "app"))
    launcher = _write_launcher()
    installed.append(str(launcher))
    unit = _write_unit()
    installed.append(str(unit))
    desktop = _write_desktop()
    installed.append(str(desktop))
    installed.extend(_enable_unit())
    mode = shortcut_mode(
        os.environ.get("XDG_CURRENT_DESKTOP", ""),
        os.environ.get("WAYLAND_DISPLAY"),
        os.environ.get("DISPLAY"),
    )
    installed.extend(_install_shortcut(mode, launcher))
    print("Installed:")
    for line in installed:
        print(line)


def _gtk_imports() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


def _print_packages() -> None:
    if shutil.which("dnf"):
        print(
            "sudo dnf install python3 python3-gobject gtk4 "
            "gobject-introspection gdk-pixbuf2 xdotool"
        )
    else:
        print(
            "sudo apt install python3 python3-gi gir1.2-gtk-4.0 "
            "gir1.2-gdkpixbuf-2.0 xdotool"
        )


def _source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _copy_app() -> None:
    dest = Path.home() / ".local" / "share" / "winclip" / "app"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("winclip", "extension"):
        target = dest / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            _source_root() / name,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )


def _write_launcher() -> Path:
    path = Path.home() / ".local" / "bin" / "winclip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'export PYTHONPATH="$HOME/.local/share/winclip/app:${PYTHONPATH}"\n'
        'exec /usr/bin/python3 -m winclip "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_unit() -> Path:
    path = Path.home() / ".config" / "systemd" / "user" / "winclip.service"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNIT, encoding="utf-8")
    return path


def _write_desktop() -> Path:
    directory = Path.home() / ".local" / "share" / "applications"
    directory.mkdir(parents=True, exist_ok=True)
    launcher = Path.home() / ".local" / "bin" / "winclip"
    desktop = directory / "winclip.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Clipboard\n"
        "Comment=WinClip clipboard history\n"
        f"Exec={launcher} toggle\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "StartupNotify=false\n",
        encoding="utf-8",
    )
    return desktop


def _enable_unit() -> list[str]:
    probe = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        check=False,
        capture_output=True,
    )
    if probe.returncode != 0:
        return ["The unit is installed and the daemon was not started."]
    names = [
        name
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")
        if os.environ.get(name)
    ]
    if names:
        subprocess.run(
            ["systemctl", "--user", "import-environment", *names],
            check=False,
        )
    enabled = subprocess.run(
        ["systemctl", "--user", "enable", "--now", "winclip.service"],
        check=False,
    )
    if enabled.returncode != 0:
        return ["The unit is installed and the daemon was not started."]
    subprocess.run(
        ["systemctl", "--user", "restart", "winclip.service"],
        check=False,
    )
    return ["winclip.service is enabled"]


def _install_shortcut(mode: str, launcher: Path) -> list[str]:
    if mode == "plasma":
        return _install_plasma(launcher)
    if mode == "extension":
        return _install_extension()
    if mode == "grab":
        return [
            "Shortcut backend: x11 grab",
            "Super+V is grabbed by the WinClip daemon.",
        ]
    return ["Shortcut backend: unbound", "Super+V was not bound. No display is available."]


def _install_plasma(launcher: Path) -> list[str]:
    directory = Path.home() / ".local" / "share" / "kglobalaccel"
    directory.mkdir(parents=True, exist_ok=True)
    desktop = directory / WINCLIP_DESKTOP
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Clipboard\n"
        f"Exec={launcher} toggle\n"
        "X-KDE-GlobalAccel-CommandShortcut=true\n"
        "X-KDE-Shortcuts=Meta+V\n"
        "StartupNotify=false\n",
        encoding="utf-8",
    )
    config = Path.home() / ".config" / "kglobalshortcutsrc"
    config.parent.mkdir(parents=True, exist_ok=True)
    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    config.write_text(apply_plasma_shortcuts(existing), encoding="utf-8")
    _reload_kglobalaccel()
    return [
        "Shortcut backend: plasma (kglobalaccel)",
        str(desktop),
        str(config),
        'Plasma action "Show Clipboard Items at Mouse Position" was removed from Super+V.',
        "Super still opens the application launcher.",
    ]


def _reload_kglobalaccel() -> None:
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if qdbus is None:
        return
    subprocess.run(
        [
            qdbus,
            "org.kde.kglobalaccel",
            "/kglobalaccel",
            "org.kde.KGlobalAccel.getComponent",
            WINCLIP_DESKTOP,
        ],
        check=False,
        capture_output=True,
    )
    for component in ("plasmashell", "org.kde.klipper.desktop", "klipper"):
        subprocess.run(
            [
                qdbus,
                "org.kde.kglobalaccel",
                f"/component/{component}",
                "org.kde.kglobalaccel.Component.setShortcut",
                "show-on-mouse-pos",
                "none",
                "2",
            ],
            check=False,
            capture_output=True,
        )


def _install_extension() -> list[str]:
    if shutil.which("gnome-extensions") is None:
        print("gnome-extensions is not installed.", file=sys.stderr)
        raise SystemExit(1)
    source = Path.home() / ".local" / "share" / "winclip" / "app" / "extension"
    dest = (
        Path.home()
        / ".local"
        / "share"
        / "gnome-shell"
        / "extensions"
        / "winclip@winclip.local"
    )
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    enabled = subprocess.run(
        ["gnome-extensions", "enable", "winclip@winclip.local"],
        check=False,
        capture_output=True,
        text=True,
    )
    if enabled.returncode != 0:
        detail = (enabled.stderr or enabled.stdout or "gnome-extensions enable failed").strip()
        print(detail, file=sys.stderr)
        raise SystemExit(enabled.returncode or 1)
    return [
        "Shortcut backend: gnome-shell extension",
        str(dest),
        "Super+V runs winclip toggle. Super still opens the overview.",
    ]
