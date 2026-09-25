"""Install the user service, launcher, desktop file, and optional GNOME pieces."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from winclip.shortcut import shortcut_mode

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

CUSTOM_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/winclip/"
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"


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
    installed.extend(_install_extension())
    installed.extend(_install_keybinding())
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


def _install_extension() -> list[str]:
    if shutil.which("gnome-shell") is None:
        return []
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
    schemas = dest / "schemas"
    compiler = shutil.which("glib-compile-schemas")
    if compiler and schemas.is_dir():
        subprocess.run([compiler, str(schemas)], check=False)
    subprocess.run(
        ["gnome-extensions", "disable", "winclip@winclip.local"],
        check=False,
    )
    subprocess.run(
        ["gnome-extensions", "enable", "winclip@winclip.local"],
        check=False,
    )
    return [
        str(dest),
        "Super+V runs winclip toggle. Super still opens the overview.",
    ]


def _install_keybinding() -> list[str]:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(MEDIA_KEYS, True) if source is not None else None
    mode = shortcut_mode(
        schema is not None,
        os.environ.get("DISPLAY"),
        os.environ.get("WAYLAND_DISPLAY"),
    )
    if mode == "grab":
        return ["Super+V is grabbed by the WinClip daemon."]
    if mode == "extension":
        return ["Super+V is bound by the WinClip GNOME Shell extension."]
    if mode == "unbound":
        return ["Super+V was not bound. No display is available."]
    settings = Gio.Settings.new(MEDIA_KEYS)
    current = list(settings.get_strv("custom-keybindings"))
    if CUSTOM_PATH not in current:
        current.append(CUSTOM_PATH)
        settings.set_strv("custom-keybindings", current)
    child = Gio.Settings.new_with_path(
        "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding",
        CUSTOM_PATH,
    )
    child.set_string("name", "Clipboard")
    child.set_string("command", "winclip toggle")
    child.set_string("binding", "<Super>v")
    shell = source.lookup("org.gnome.shell.keybindings", True) if source is not None else None
    if shell is not None and shell.has_key("toggle-message-tray"):
        tray = Gio.Settings.new("org.gnome.shell.keybindings")
        bindings = [item for item in tray.get_strv("toggle-message-tray") if item != "<Super>v"]
        tray.set_strv("toggle-message-tray", bindings)
    return ["Super+V runs winclip toggle."]
