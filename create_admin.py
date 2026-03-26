from app import create_app
from backend.database import db
from models import User
from werkzeug.security import generate_password_hash

app = create_app()

with app.app_context():
    db.create_all()
    
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(username='admin', email='admin@cdac.in', password_hash=generate_password_hash('sahil22112001'), role='admin', employee_id='admin01')
        db.session.add(admin)
        db.session.commit()
        print('Admin created successfully!')
    else:
        print('Admin already exists!')
