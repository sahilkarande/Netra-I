from flask import Flask
from .auth import auth_bp
from .admin import admin_bp
from .faculty import faculty_bp
from .student import student_bp
from .exam import exam_bp

def register_blueprints(app: Flask):
    """Register all blueprints with the Flask application"""
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(faculty_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(exam_bp)
    
    print("✅ All blueprints registered successfully!")
