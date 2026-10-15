#!/bin/bash
# Install the nginx reverse proxy for Netra-I.
# Usage: bash deploy/deploy_nginx.sh   (run from anywhere)
set -e

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"

if [ ! -f "$PROJECT_DIR/certs/cert.pem" ] || [ ! -f "$PROJECT_DIR/certs/key.pem" ]; then
    echo "certs/cert.pem or certs/key.pem missing - run: bash scripts/generate_lan_cert.sh"
    exit 1
fi

echo "Copying nginx configuration..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$DEPLOY_DIR/nginx_netra_i.conf" \
    | sudo tee /etc/nginx/sites-available/netra-i > /dev/null

echo "Deploying symlink..."
sudo ln -sf /etc/nginx/sites-available/netra-i /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

echo "Testing nginx setup..."
sudo nginx -t

echo "Restarting Nginx..."
sudo systemctl restart nginx

echo "Nginx successfully configured!"
