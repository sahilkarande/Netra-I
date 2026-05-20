import time
import json
import cv2
import numpy as np
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from flask_socketio import emit

from backend.database import db
from backend.models import Exam, Question, StudentExam, StudentAnswer, ActivityLog, ExamViolation, ExamCalibration
from backend.socket_io import socketio
from backend.services.proctor_service import (
    get_proctor_instance, detect_faces, decode_base64_image, 
    log_proctor_event, _heartbeat_registry, _last_frame_processed
)
from backend.services.exam_service import calculate_student_score

exam_bp = Blueprint('exam', __name__)

@exam_bp.route("/start-exam/<int:exam_id>", methods=["POST", "GET"])
@login_required
def start_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    
    # Check if student already has a session
    student_exam = StudentExam.query.filter_by(
        student_id=current_user.id, 
        exam_id=exam_id
    ).first()
    
    if not student_exam:
        student_exam = StudentExam(
            student_id=current_user.id,
            exam_id=exam_id,
            status='started',
            started_at=datetime.utcnow()
        )
        db.session.add(student_exam)
        db.session.commit()
    elif student_exam.completed:
        flash('You have already completed this exam.', 'info')
        return redirect(url_for('student.student_dashboard'))
        
    return redirect(url_for('exam.take_exam', exam_id=exam_id))

@exam_bp.route('/exam/<int:exam_id>/take')
@login_required
def take_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    student_exam = StudentExam.query.filter_by(
        student_id=current_user.id, 
        exam_id=exam_id
    ).first_or_404()
    
    if student_exam.completed:
        return redirect(url_for('student.student_dashboard'))
        
    questions = Question.query.filter_by(exam_id=exam_id).all()
    return render_template('exam/take_exam.html', exam=exam, student_exam=student_exam, questions=questions)

@exam_bp.route('/api/save-answer', methods=['POST'])
@login_required
def save_answer():
    data = request.get_json()
    student_exam_id = data.get('student_exam_id')
    question_id = data.get('question_id')
    selected_answer = data.get('selected_answer')
    
    answer = StudentAnswer.query.filter_by(
        student_exam_id=student_exam_id, 
        question_id=question_id
    ).first()
    
    if not answer:
        answer = StudentAnswer(
            student_exam_id=student_exam_id,
            question_id=question_id
        )
        db.session.add(answer)
        
    answer.selected_answer = selected_answer
    db.session.commit()
    return jsonify({"success": True})

@exam_bp.route('/submit_exam/<int:student_exam_id>', methods=['POST', 'GET'])
@login_required
def submit_exam(student_exam_id):
    student_exam = StudentExam.query.get_or_404(student_exam_id)
    if student_exam.student_id != current_user.id:
        return "Unauthorized", 403
        
    if not student_exam.completed:
        student_exam.completed = True
        student_exam.submitted_at = datetime.utcnow()
        calculate_student_score(student_exam_id)
        db.session.commit()
        
    return redirect(url_for('student.student_exam_result', student_exam_id=student_exam_id))

# Socket.IO Event Handlers
@socketio.on('calibrationBinary')
def handle_calibration_binary(data):
    try:
        student_exam_id = data.get('studentExamId')
        frame_buffer = data.get('frame')
        if not frame_buffer or not student_exam_id:
            return
            
        nparr = np.frombuffer(frame_buffer, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None: return
        
        faces = detect_faces(frame)
        student_exam = StudentExam.query.get(student_exam_id)
        
        if len(faces) == 1 and student_exam:
            exam = Exam.query.get(student_exam.exam_id)
            proctor_state, vision = get_proctor_instance(student_exam_id, exam)
            vision.calibrate([frame] * 5)
            student_exam.calibration_completed = True
            db.session.commit()
            emit('calibration_result', {'success': True, 'message': 'Calibrated!'})
        else:
            emit('calibration_result', {'success': False, 'message': 'Face error'})
    except Exception as e:
        print(f"Calibration error: {e}")

@socketio.on('frameBinary')
def handle_frame_binary(data):
    try:
        student_exam_id = data.get('studentExamId')
        frame_buffer = data.get('frame')
        if not frame_buffer or not student_exam_id: return
        
        nparr = np.frombuffer(frame_buffer, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None: return
        
        current_time = time.time()
        if current_time - _last_frame_processed.get(student_exam_id, 0) < 4.0: return
        _last_frame_processed[student_exam_id] = current_time
        
        student_exam = StudentExam.query.get(student_exam_id)
        if not student_exam: return
        
        exam = Exam.query.get(student_exam.exam_id)
        proctor_state, vision = get_proctor_instance(student_exam_id, exam)
        status, details = vision.check_frame(frame)
        
        if status in ("WARNING", "TERMINATE"):
            student_exam.total_violations = (student_exam.total_violations or 0) + 1
            log_proctor_event(student_exam_id, details.get('violation_type', 'unknown'), 'medium', details.get('message'))
            db.session.commit()
            emit('proctor_result', {'success': False, 'message': details.get('message'), 'count': student_exam.total_violations})
        else:
            emit('proctor_result', {'success': True})
            
    except Exception as e:
        print(f"Frame error: {e}")

@socketio.on('heartbeat')
def handle_heartbeat(data):
    student_exam_id = data.get('studentExamId')
    if student_exam_id:
        _heartbeat_registry[student_exam_id] = time.time()
        emit('heartbeat_ack', {'timestamp': time.time()})
