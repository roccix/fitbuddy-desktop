#!/usr/bin/env bash
# Installs FitBuddy Desktop for the current user (no sudo): sync service, launcher, icon.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_id="dev.roccix.FitBuddyDesktop"
python="$(command -v python3)"

install -Dm644 "$repo/data/$app_id.png" "$HOME/.local/share/icons/hicolor/256x256/apps/$app_id.png"

install -d "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/$app_id.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=FitBuddy
GenericName=Food diary
Comment=Meals, workouts and weight synced with FitBuddy on your phone
Exec=env PYTHONPATH=$repo $python -m fitbuddy_desktop
Icon=$app_id
Categories=Utility;
Keywords=calories;diet;fitness;meals;nutrition;
StartupNotify=true
DESKTOP

install -d "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/fitbuddy-sync.service" <<UNIT
[Unit]
Description=FitBuddy Desktop sync endpoint for the phone
After=network-online.target

[Service]
Environment=PYTHONPATH=$repo
ExecStart=$python -m fitbuddy_desktop serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now fitbuddy-sync.service
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true

echo "Sync service: $(systemctl --user is-active fitbuddy-sync.service)"
echo "Open FitBuddy from your app launcher, then Settings → Pair phone."
