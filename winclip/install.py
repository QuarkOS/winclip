"""Install the user service, launcher, desktop file, and optional GNOME pieces."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

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
    _copy_app()
    _write_launcher()
    _write_unit()
    _write_desktop()
    _enable_unit()
    _install_extension()
    _install_keybinding()


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


def _write_launcher() -> None:
    path = Path.home() / ".local" / "bin" / "winclip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'export PYTHONPATH="$HOME/.local/share/winclip/app:${PYTHONPATH}"\n'
        'exec /usr/bin/python3 -m winclip "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_unit() -> None:
    path = Path.home() / ".config" / "systemd" / "user" / "winclip.service"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNIT, encoding="utf-8")


def _write_desktop() -> None:
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


def _enable_unit() -> None:
    probe = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        check=False,
        capture_output=True,
    )
    if probe.returncode != 0:
        print("The unit is installed and the daemon was not started.")
        return
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
        print("The unit is installed and the daemon was not started.")


def _install_extension() -> None:
    if shutil.which("gnome-shell") is None:
        return
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
    subprocess.run(
        ["gnome-extensions", "enable", "winclip@winclip.local"],
        check=False,
    )
    print("A Wayland session loads the extension at the next login.")


def _install_keybinding() -> None:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(MEDIA_KEYS, True) if source is not None else None
    if schema is None:
        print("org.gnome.settings-daemon.plugins.media-keys is not installed.")
        return
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
