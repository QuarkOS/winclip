# WinClip

Windows-style clipboard history for Fedora GNOME on Wayland, and for X11. History stays off until you press **Turn on**. Pinned items stay after **Clear all** and after a reboot. Unpinned items do not.

Press Super+V to open the panel. It sits at the bottom center of the screen. Activating a row copies that item and pastes it into the window that was focused before the panel opened.

## Fedora

Log out and back in after install so GNOME Shell loads the extension.

```sh
sudo dnf install python3 python3-gobject gtk4 gobject-introspection gdk-pixbuf2 xdotool
python3 -m winclip install
```

Super+V runs `winclip toggle`.

## This machine

GNOME Shell and the media-keys schema are optional. Install still finishes when that schema is missing. On Wayland the extension binds Super+V. On X11 the daemon grabs it.

```sh
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-gdkpixbuf-2.0 xdotool
python3 -m winclip install
python3 -m winclip run
python3 -m winclip toggle
```

`python3 -m winclip` starts the daemon. `toggle` opens or closes the panel.

History rules can be imported with no display:

```sh
python3 -m unittest tests.test_history tests.test_wire
```
