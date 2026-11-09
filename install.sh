#!/bin/bash

# Auto-re-execute with bash if launched via sh/dash
if [ -z "$BASH_VERSION" ]; then
    exec bash "$0" "$@"
fi

set -e

DIR="$( cd "$( dirname "$0" )" && pwd )"
cd "$DIR"

# ==========================================================
#                  Authorization Gate
# ==========================================================
SECRET_PASSPHRASE="123456"

read -s -p "Enter authorization key to install CDDM: " USER_INPUT
echo ""

if [ "$USER_INPUT" != "$SECRET_PASSPHRASE" ]; then
    echo "❌ Unauthorized: Invalid key. Installation aborted."
    exit 1
fi
echo "✓ Authorized. Proceeding with installation..."
echo ""

echo "=========================================================="
echo "          Installing CDDM Live & Desktop Launcher         "
echo "=========================================================="

# 1. Check for Python 3 and python3-venv
if ! command -v python3 &>/dev/null || ! python3 -m venv --help &>/dev/null; then
    echo "(!) Python 3 or python3-venv is missing."
    echo "Installing required system packages (sudo password may be required)..."
    sudo apt update && sudo apt install -y python3 python3-venv python3-pip
fi

# 2. Rebuild local virtual environment if missing or broken
if [ ! -d "$DIR/venv" ] || [ ! -x "$DIR/venv/bin/python3" ]; then
    echo "Creating virtual environment..."
    rm -rf "$DIR/venv"
    python3 -m venv "$DIR/venv"
fi

# 3. Upgrade pip and install requirements
echo "Installing Python dependencies (PySide6, requests, certifi)..."
"$DIR/venv/bin/pip" install --upgrade pip

if [ -f "$DIR/requirements.txt" ]; then
    "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"
else
    "$DIR/venv/bin/pip" install "PySide6>=6.5.0" "requests>=2.28.0" "certifi>=2023.5.7"
fi

chmod +x "$DIR/run_cddm.sh" "$DIR/install.sh" 2>/dev/null || true

# 4. Register application in Ubuntu App Launcher (~/.local/share/applications)
echo "Registering CDDM Live in Ubuntu Application Menu..."
mkdir -p "$HOME/.local/share/applications"

cat << DESKTOP_EOF > "$HOME/.local/share/applications/cddm.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=CDDM Live
GenericName=GNSS Archive Monitor
Comment=NASA CDDIS GNSS Telemetry & Archive Monitor
Exec=$DIR/run_cddm.sh
Icon=$DIR/program/cddis_icon.png
Path=$DIR
Terminal=false
Categories=Science;Geoscience;Education;
StartupNotify=true
DESKTOP_EOF

chmod +x "$HOME/.local/share/applications/cddm.desktop"

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

echo "=========================================================="
echo "✓ Installation complete!"
echo "• Launch via terminal: ./run_cddm.sh"
echo "• Or search 'CDDM Live' in Ubuntu's Applications menu!"
echo "=========================================================="
