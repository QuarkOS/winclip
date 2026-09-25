# WinClip

Windows-style clipboard history for Fedora GNOME on Wayland, and for X11. History stays off until you press **Turn on**. Pinned items stay after **Clear all** and after a reboot. Unpinned items do not.

Press Super+V to open the panel. It sits at the bottom center of the screen. Activating a row copies that item and pastes it into the window that was focused before the panel opened.

## Fedora

Install picks the shortcut from the running session. On Plasma Wayland, Super+V is a KGlobalAccel command shortcut, the same mechanism Klipper uses for Meta+V. Klipper's "Show Clipboard Items at Mouse Position" is taken off that chord. Super alone stays the application launcher.

```sh
sudo dnf install python3 python3-gobject gtk4 gobject-introspection gdk-pixbuf2 xdotool
python3 -m winclip install
```

On GNOME Wayland the shell extension is the only Super+V binding, and install fails if `gnome-extensions enable` cannot talk to the shell. Outside both, the daemon grabs Super+V on X11.

## This machine

GNOME Shell is optional. Install names the backend it used. On GNOME the extension runs `winclip toggle` for Super+V and leaves the Super overview key alone. On Plasma it writes a kglobalaccel desktop file. Outside both, the daemon grabs Super+V on X11.

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
