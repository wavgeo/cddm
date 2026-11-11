#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=========================================================="
echo "          CDDM Live - Automated Linux Installer           "
echo "=========================================================="

# 1. Detect or Configure Institutional Proxy
if [ -n "$http_proxy" ] || [ -n "$HTTP_PROXY" ]; then
    DETECTED_PROXY="${http_proxy:-$HTTP_PROXY}"
    echo "Detected system proxy: $DETECTED_PROXY"
    read -p "Use this proxy for CDDM? [Y/n]: " USE_SYS_PROXY
    if [[ ! "$USE_SYS_PROXY" =~ ^[Nn] ]]; then
        echo "$DETECTED_PROXY" > proxy.conf
        echo "✓ Saved proxy configuration."
    fi
elif [ ! -f "proxy.conf" ]; then
    echo "No proxy configuration detected."
    read -p "Are you behind an institutional proxy (e.g., CSIR)? [y/N]: " IS_INST
    if [[ "$IS_INST" =~ ^[Yy] ]]; then
        read -p "Enter proxy URL (e.g., http://14.139.134.20:3128): " USER_PROXY
        if [ -n "$USER_PROXY" ]; then
            echo "$USER_PROXY" > proxy.conf
            echo "✓ Saved proxy configuration."
        fi
    else
        echo "DIRECT" > proxy.conf
    fi
fi

# 2. Setup Virtual Environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Installing/verifying dependencies..."
venv/bin/pip install --upgrade pip >/dev/null 2>&1
venv/bin/pip install PySide6 requests certifi >/dev/null 2>&1
echo "✓ Python environment ready."

# 3. Create Desktop Launcher with exact dynamic paths
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

cat << DESKTOP_EOF > "$DESKTOP_DIR/cddm.desktop"
[Desktop Entry]
Version=1.1
Type=Application
Name=CDDM Live v1.1
GenericName=GNSS CDDIS Monitor
Comment=NASA CDDIS GNSS Station Upload Availability Monitor
Exec=$DIR/run_cddm.sh
Path=$DIR
Icon=$DIR/program/cddis_icon.png
Terminal=false
Categories=Science;Geology;Engineering;
StartupNotify=true
StartupWMClass=cddm
DESKTOP_EOF

chmod +x "$DESKTOP_DIR/cddm.desktop"
chmod +x run_cddm.sh
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
echo "✓ Application shortcut added to App Menu."

echo "=========================================================="
echo " [SUCCESS] CDDM Live installation completed!"
echo " Launch it from your App Menu or by running ./run_cddm.sh"
echo "=========================================================="
