#!/bin/bash
# Creates an application-menu entry and (if ~/Desktop exists) a Desktop
# icon for the NYC DOE Record Anonymizer, pointing at THIS checkout's
# location. Safe to re-run any time (e.g. after moving the clone).
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$DIR/run_anonymizer.sh"
chmod +x "$LAUNCHER" "$DIR/record_anonymizer.py"

DESKTOP_ENTRY="[Desktop Entry]
Type=Application
Name=NYC DOE Record Anonymizer
Comment=Pseudonymize student record PDFs before upload; restore real names after
Exec=$LAUNCHER
Path=$DIR
Icon=security-high
Terminal=false
Categories=Utility;
"

APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
printf '%s' "$DESKTOP_ENTRY" > "$APPS_DIR/nyc-doe-anonymizer.desktop"
chmod +x "$APPS_DIR/nyc-doe-anonymizer.desktop"
echo "Added to application menu: $APPS_DIR/nyc-doe-anonymizer.desktop"

if [ -d "$HOME/Desktop" ]; then
    printf '%s' "$DESKTOP_ENTRY" > "$HOME/Desktop/nyc-doe-anonymizer.desktop"
    chmod +x "$HOME/Desktop/nyc-doe-anonymizer.desktop"
    # GNOME/Nautilus won't run a Desktop launcher until it's marked trusted;
    # this is a no-op (and harmless) on desktops that don't need it.
    if command -v gio >/dev/null 2>&1; then
        gio set "$HOME/Desktop/nyc-doe-anonymizer.desktop" metadata::trusted true 2>/dev/null || true
    fi
    echo "Added Desktop shortcut: $HOME/Desktop/nyc-doe-anonymizer.desktop"
    echo "(On GNOME you may need to right-click it once and choose 'Allow Launching'."
    echo " On KDE Plasma, right-click and choose 'Trust This Application' if prompted.)"
else
    echo "No ~/Desktop directory found — app-menu entry only."
fi

echo
echo "Done. Search 'NYC DOE' in your application menu, or use the Desktop icon."
echo "(Icon may render as a generic placeholder — cosmetic only.)"
