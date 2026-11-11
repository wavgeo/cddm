#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# 1. Dynamically load proxy from proxy.conf if present (for desktop/GUI launches)
PROXY_FILE="$DIR/proxy.conf"
if [ -f "$PROXY_FILE" ]; then
    P_VAL=$(tr -d '[:space:]' < "$PROXY_FILE")
    if [ -n "$P_VAL" ] && [ "$P_VAL" != "DIRECT" ]; then
        if [[ ! "$P_VAL" =~ ^https?:// ]]; then
            P_VAL="http://$P_VAL"
        fi
        export http_proxy="$P_VAL"
        export https_proxy="$P_VAL"
        export HTTP_PROXY="$P_VAL"
        export HTTPS_PROXY="$P_VAL"
        export no_proxy="localhost,127.0.0.1,::1"
        export NO_PROXY="localhost,127.0.0.1,::1"
    fi
fi

# 2. Self-Healing Check: Verify if venv exists and can import PySide6 & requests
VENV_PY="$DIR/venv/bin/python3"
NEEDS_REBUILD=0

if [ ! -x "$VENV_PY" ]; then
    NEEDS_REBUILD=1
elif ! "$VENV_PY" -c "import requests, PySide6" >/dev/null 2>&1; then
    NEEDS_REBUILD=1
fi

if [ $NEEDS_REBUILD -eq 1 ]; then
    echo "=========================================================="
    echo "  System update detected! Auto-repairing CDDM environment..."
    echo "=========================================================="
    rm -rf "$DIR/venv"
    python3 -m venv "$DIR/venv"
    "$DIR/venv/bin/pip" install --upgrade pip >/dev/null 2>&1
    "$DIR/venv/bin/pip" install requests PySide6 certifi
    echo "✓ Repair complete. Launching CDDM..."
fi

exec "$VENV_PY" "$DIR/program/cddis_monitor.py" "$@"
