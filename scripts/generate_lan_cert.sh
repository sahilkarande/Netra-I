#!/bin/bash
# ============================================================================
# generate_lan_cert.sh
# Generates a self-signed SSL certificate valid for:
#   - localhost
#   - 127.0.0.1
#   - The machine's LAN IP address(es)
#
# This is REQUIRED because getUserMedia() (camera access) requires a
# "secure context" (HTTPS or localhost). When students connect over LAN
# using the network IP (e.g. http://192.168.1.100:8800), the browser
# blocks camera access entirely.
#
# Usage:
#   chmod +x scripts/generate_lan_cert.sh
#   ./scripts/generate_lan_cert.sh
#
# Output: cert.pem and key.pem in the project root directory
# ============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CERT_FILE="$PROJECT_DIR/cert.pem"
KEY_FILE="$PROJECT_DIR/key.pem"
DAYS_VALID=365

echo "============================================"
echo " ProctoGuard LAN SSL Certificate Generator"
echo "============================================"
echo ""

# Detect LAN IP addresses
echo "Detecting LAN IP addresses..."
LAN_IPS=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | head -5)

if [ -z "$LAN_IPS" ]; then
    echo "WARNING: Could not detect LAN IP. Using 0.0.0.0 as fallback."
    LAN_IPS="0.0.0.0"
fi

echo "Detected LAN IPs:"
echo "$LAN_IPS"
echo ""

# Build SAN (Subject Alternative Name) entries
SAN_ENTRIES="DNS:localhost"
IP_INDEX=1

# Always include 127.0.0.1
SAN_ENTRIES="$SAN_ENTRIES,IP:127.0.0.1"
IP_INDEX=$((IP_INDEX + 1))

# Add each detected LAN IP
for ip in $LAN_IPS; do
    SAN_ENTRIES="$SAN_ENTRIES,IP:$ip"
    IP_INDEX=$((IP_INDEX + 1))
done

echo "SAN entries: $SAN_ENTRIES"
echo ""

# Allow user to add custom IPs
read -p "Add additional IP addresses? (comma-separated, or press Enter to skip): " EXTRA_IPS
if [ -n "$EXTRA_IPS" ]; then
    IFS=',' read -ra EXTRA_ARRAY <<< "$EXTRA_IPS"
    for ip in "${EXTRA_ARRAY[@]}"; do
        ip=$(echo "$ip" | tr -d ' ')
        SAN_ENTRIES="$SAN_ENTRIES,IP:$ip"
    done
    echo "Updated SAN entries: $SAN_ENTRIES"
fi

echo ""
echo "Generating SSL certificate..."

# Create OpenSSL config with SAN
OPENSSL_CNF=$(mktemp)
cat > "$OPENSSL_CNF" << EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
x509_extensions = v3_req
distinguished_name = dn

[dn]
C = IN
ST = Maharashtra
L = Pune
O = ProctoGuard Exam Platform
OU = IT Department
CN = ProctoGuard LAN Server

[v3_req]
subjectAltName = $SAN_ENTRIES
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF

# Generate certificate
openssl req -x509 -nodes \
    -days $DAYS_VALID \
    -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -config "$OPENSSL_CNF"

# Cleanup temp config
rm -f "$OPENSSL_CNF"

echo ""
echo "============================================"
echo " Certificate Generated Successfully!"
echo "============================================"
echo ""
echo "Files:"
echo "  Certificate: $CERT_FILE"
echo "  Private Key: $KEY_FILE"
echo "  Valid for:   $DAYS_VALID days"
echo ""
echo "SAN entries:   $SAN_ENTRIES"
echo ""
echo "============================================"
echo " IMPORTANT: Student Browser Setup"
echo "============================================"
echo ""
echo "Since this is a self-signed certificate, students need to:"
echo ""
echo "  Option 1 (Chrome - Easiest for LAN):"
echo "    Navigate to: chrome://flags/#allow-insecure-localhost"
echo "    Enable the flag and restart Chrome."
echo ""
echo "  Option 2 (Any browser):"
echo "    1. Open https://<SERVER_IP>:8800 in browser"
echo "    2. Click 'Advanced' > 'Proceed to site (unsafe)'"
echo "    3. Camera permissions will then work normally"
echo ""
echo "  Option 3 (Chrome enterprise flag for LAN IPs):"
echo "    Launch Chrome with:"
echo "    google-chrome --unsafely-treat-insecure-origin-as-secure=\"http://YOUR_LAN_IP:8800\""
echo ""
echo "============================================"
