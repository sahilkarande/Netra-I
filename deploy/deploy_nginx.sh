#!/bin/bash
echo "Copying nginx configuration..."
sudo cp nginx_netra_i.conf /etc/nginx/sites-available/netra-i

echo "Deploying symlink..."
sudo ln -sf /etc/nginx/sites-available/netra-i /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

echo "Testing nginx setup..."
sudo nginx -t

echo "Restarting Nginx..."
sudo systemctl restart nginx

echo "Nginx successfully configured!"
