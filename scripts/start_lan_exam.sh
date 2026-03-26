#!/bin/bash
# ============================================================================
# start_lan_exam.sh
# One-command launcher for LAN exam deployment with HTTPS
#
# Usage:
#   chmod +x scripts/start_lan_exam.sh
#   ./scripts/start_lan_exam.sh
# ============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CERT_FILE="$PROJECT_DIR/cert.pem"
KEY_FILE="$PROJECT_DIR/key.pem"
PORT=${PORT:-8800}

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          ProctoGuard - LAN Exam Server Launcher             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Check if SSL certs exist, generate if not
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "⚠️  SSL certificates not found. Generating..."
    echo ""
    bash "$PROJECT_DIR/scripts/generate_lan_cert.sh"
    echo ""
fi

# Detect LAN IP
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$LAN_IP" ]; then
    LAN_IP="<your-ip>"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Server Configuration                                       ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Local URL:   https://localhost:$PORT                        ║"
echo "║  LAN URL:     https://$LAN_IP:$PORT                         ║"
echo "║  Protocol:    HTTPS (camera access enabled)                  ║"
echo "║                                                              ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Student Instructions:                                       ║"
echo "║                                                              ║"
echo "║  1. Open Chrome/Edge browser                                 ║"
echo "║  2. Navigate to: https://$LAN_IP:$PORT                      ║"
echo "║  3. Click 'Advanced' > 'Proceed to site'                    ║"
echo "║  4. Allow camera when prompted                               ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Activate virtualenv if it exists
if [ -f "$PROJECT_DIR/venv/bin/activate" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
fi

# Start the server
cd "$PROJECT_DIR"
python app.py --ssl
