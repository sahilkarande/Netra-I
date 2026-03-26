#!/usr/bin/env python3
"""
Create an admin user for ProctoGuard.
Admin can see all exams and students across all faculties.

Usage:
    python scripts/create_admin.py

Or with custom credentials:
    python scripts/create_admin.py --username superadmin --email admin@proctoguard.com --password Admin@123
"""

import sys
import os
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import User, db
from werkzeug.security import generate_password_hash

app = create_app()


def create_admin(username='admin', email='admin@proctoguard.com', password='Admin@123', full_name='System Admin'):
    """Create an admin user."""
    with app.app_context():
        # Check if admin already exists
        existing = User.query.filter_by(username=username).first()
        if existing:
            if existing.role == 'admin':
                print(f"⚠️  Admin '{username}' already exists.")
                return
            else:
                print(f"⚠️  User '{username}' exists with role '{existing.role}'. Upgrading to admin...")
                existing.role = 'admin'
                db.session.commit()
                print(f"✅ User '{username}' upgraded to admin role.")
                return

        # Create new admin
        admin = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password, method='pbkdf2:sha256'),
            role='admin',
            full_name=full_name,
            is_verified=True,
            password_changed=True
        )

        db.session.add(admin)
        db.session.commit()

        print(f"""
✅ Admin user created successfully!
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Username:  {username}
   Email:     {email}
   Password:  {password}
   Role:      admin
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   
   Admin privileges:
   • View ALL exams from all faculties
   • Access ALL student data
   • Manage ALL exam operations
   • Full SQL console access
""")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create ProctoGuard admin user')
    parser.add_argument('--username', default='admin', help='Admin username (default: admin)')
    parser.add_argument('--email', default='admin@proctoguard.com', help='Admin email')
    parser.add_argument('--password', default='Admin@123', help='Admin password')
    parser.add_argument('--name', default='System Admin', help='Full name')

    args = parser.parse_args()
    create_admin(args.username, args.email, args.password, args.name)
