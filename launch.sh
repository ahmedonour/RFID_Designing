#!/bin/bash
# RFID Asset Manager — macOS launcher
# Double-click this file to start the app (or run: bash launch.sh)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "══════════════════════════════════════════"
echo "  RFID Asset Manager — Startup"
echo "══════════════════════════════════════════"

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌  Python 3 not found. Install from https://python.org"
    exit 1
fi

echo "✔  Python: $(python3 --version)"

# Install deps if missing
echo "→  Checking dependencies…"
python3 -c "import customtkinter" 2>/dev/null || {
    echo "→  Installing customtkinter…"
    pip3 install customtkinter --break-system-packages 2>/dev/null || pip3 install customtkinter
}
python3 -c "import PIL" 2>/dev/null || {
    echo "→  Installing Pillow…"
    pip3 install Pillow --break-system-packages 2>/dev/null || pip3 install Pillow
}
python3 -c "import qrcode" 2>/dev/null || {
    echo "→  Installing qrcode…"
    pip3 install "qrcode[pil]" --break-system-packages 2>/dev/null || pip3 install "qrcode[pil]"
}
python3 -c "import reportlab" 2>/dev/null || {
    echo "→  Installing reportlab…"
    pip3 install reportlab --break-system-packages 2>/dev/null || pip3 install reportlab
}

echo "✔  All dependencies ready"
echo "→  Launching app…"
echo ""

python3 rfid_manager.py
