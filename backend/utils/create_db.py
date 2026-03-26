"""
Simple Database Creation Script
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash
from datetime import datetime
import random
import string
import re

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///exam_platform.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager(app)

# Define Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    
    # OTP fields
    is_verified = db.Column(db.Boolean, default=False)
    otp = db.Column(db.String(6))
    otp_created_at = db.Column(db.DateTime)
    
    # Student fields
    prn_number = db.Column(db.String(20), unique=True)
    roll_id = db.Column(db.String(2))
    batch = db.Column(db.String(50))
    
    # Faculty fields
    employee_id = db.Column(db.String(20), unique=True)
    department = db.Column(db.String(100))
    
    # Common fields
    full_name = db.Column(db.String(200))
    phone = db.Column(db.String(15))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    duration_minutes = db.Column(db.Integer, nullable=False)
    passing_score = db.Column(db.Float, default=50.0)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    allow_tab_switch = db.Column(db.Boolean, default=False)
    max_tab_switches = db.Column(db.Integer, default=3)
    randomize_questions = db.Column(db.Boolean, default=True)
    show_results_immediately = db.Column(db.Boolean, default=True)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id'), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(500), nullable=False)
    option_b = db.Column(db.String(500), nullable=False)
    option_c = db.Column(db.String(500))
    option_d = db.Column(db.String(500))
    correct_answer = db.Column(db.String(1), nullable=False)
    points = db.Column(db.Float, default=1.0)
    order_number = db.Column(db.Integer)
    original_question = db.Column(db.Text)
    enhanced = db.Column(db.Boolean, default=False)

class StudentExam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id'), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime)
    score = db.Column(db.Float)
    total_points = db.Column(db.Float)
    percentage = db.Column(db.Float)
    passed = db.Column(db.Boolean)
    tab_switch_count = db.Column(db.Integer, default=0)
    suspicious_activity_count = db.Column(db.Integer, default=0)
    time_taken_minutes = db.Column(db.Integer)
    status = db.Column(db.String(20), default='in_progress')

class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_exam_id = db.Column(db.Integer, db.ForeignKey('student_exam.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    selected_answer = db.Column(db.String(1))
    is_correct = db.Column(db.Boolean)
    points_earned = db.Column(db.Float, default=0.0)
    answered_at = db.Column(db.DateTime, default=datetime.utcnow)

class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_exam_id = db.Column(db.Integer, db.ForeignKey('student_exam.id'), nullable=False)
    activity_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    severity = db.Column(db.String(20), default='low')

# Create database
if __name__ == '__main__':
    print("\n" + "="*70)
    print("Creating Database...")
    print("="*70)
    
    with app.app_context():
        # Delete old database if exists
        import os
        if os.path.exists('exam_platform.db'):
            os.remove('exam_platform.db')
            print("✅ Old database deleted")
        
        # Create all tables
        db.create_all()
        print("✅ Database tables created!")
        
        # Create default admin
        admin = User(
            username='admin',
            email='admin@example.com',
            password_hash=generate_password_hash('admin123'),
            role='faculty',
            employee_id='ADMIN001',
            full_name='System Administrator',
            is_verified=True
        )
        
        db.session.add(admin)
        db.session.commit()
        print("✅ Default admin created!")
        
        print("\n" + "="*70)
        print("🎉 DATABASE CREATED SUCCESSFULLY!")
        print("="*70)
        print("\n📋 Default Admin Credentials:")
        print("   Username: admin")
        print("   Password: admin123")
        print("   Role: Faculty")
        print("\n⚠️  Change password after first login!")
        print("="*70 + "\n")