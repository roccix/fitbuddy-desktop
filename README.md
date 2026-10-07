# FitBuddy Desktop

A Linux desktop companion for [FitBuddy](https://github.com/anantdark/FitBuddy), built with
GTK 4 and libadwaita and styled after the Android app. It shows the day's calorie ring,
macros, log, 30-day progress and weight trend, and lets you log meals (including saved
meals), workouts and weight from your computer. Everything stays in sync with the phone.

Foods keep their per-100 g values, so a saved food can be logged at any weight: pick it,
change the grams and kcal and macros follow. Packaged foods can also be added by barcode
(scanned with the webcam, or typed): saved foods are checked first, then
[Open Food Facts](https://world.openfoodfacts.org).

## How sync works

- The phone stays the source of truth. On each sync, FitBuddy uploads a full snapshot of
  its data to the PC. AI settings and API keys are never included.
- Entries added or deleted on the PC show up immediately on the desktop. They are queued
  until the phone applies and acknowledges them; a small cloud icon marks entries that
  haven't reached the phone yet.
- The phone syncs right away while FitBuddy is open, and about every 15 minutes in the
  background (WorkManager).
- `fitbuddy-sync.service` (a systemd user service) listens on port 8765. It only accepts
  Tailscale and localhost clients, and every request must carry the pairing code. Plain
  LAN access is off by default because that traffic is unencrypted; set
  `"allow_lan": true` in `config.json` to enable it.

State lives in `~/.local/share/fitbuddy-desktop/`: `config.json` (pairing code and port),
`snapshot.json`, `ops.json` (queue) and `meta.json`.

## Requirements

Any recent Linux desktop, on Wayland or X11: nothing is specific to a desktop environment.
You need Python 3.11+, PyGObject, GTK 4 and libadwaita 1.5+ (Ubuntu 24.04+, Fedora 40+,
Arch, openSUSE Tumbleweed; Debian 12 and Ubuntu 22.04 are too old). The webcam scanner
needs GStreamer with the `zbar` element; PipeWire is optional.

Arch-based systems such as Omarchy:

```
sudo pacman -S python-gobject gtk4 libadwaita gst-plugins-bad zbar gst-plugin-pipewire
```

Ubuntu / Debian testing:

```
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
```

Fedora:

```
sudo dnf install python3-gobject gtk4 libadwaita gstreamer1-plugins-good gstreamer1-plugins-bad-free-extras
```

## Install

```
./install.sh
```

This installs the icon, the launcher entry and the user service; no root is needed. It
records the `python3` found on `PATH`, so run it with the system Python (the one with
PyGObject), not a pyenv/mise one without it: `PATH=/usr/bin:$PATH ./install.sh`. Sync runs
as a systemd user service; without systemd, start `python -m fitbuddy_desktop serve`
another way. If you use ufw, open the port on the Tailscale interface once:

```
sudo ufw allow in on tailscale0 to any port 8765 proto tcp
```

## Pairing

Open FitBuddy Desktop, then go to Settings → Pair phone. On the phone, open FitBuddy →
Settings → PC sync, turn sync on, and enter the Tailscale address (for example
`my-pc:8765`) and the pairing code.

This needs a FitBuddy build with PC sync support (see the linked pull request).

## Development

```
python -m unittest discover -s tests -t .
python -m fitbuddy_desktop          # GUI
python -m fitbuddy_desktop serve    # sync endpoint
```

Logs: `journalctl --user -u fitbuddy-sync`.

## License

GPL-3.0, like FitBuddy. The app icon comes from FitBuddy; the UI icons are adapted from
Material Icons (Apache-2.0).
