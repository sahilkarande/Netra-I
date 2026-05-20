import random
import json
from datetime import datetime
from backend.database import db
from backend.models import StudentExam, Question, StudentAnswer

def calculate_student_score(student_exam_id):
    """Calculate and update the score for a completed student exam"""
    try:
        student_exam = StudentExam.query.get(student_exam_id)
        if not student_exam:
            return None
        
        exam = student_exam.exam
        questions = Question.query.filter_by(exam_id=exam.id).all()
        
        if not questions:
            student_exam.score = 0
            student_exam.total_points = 0
            student_exam.percentage = 0
            student_exam.passed = False
            db.session.commit()
            return {'score': 0, 'total_points': 0}
        
        student_answers = StudentAnswer.query.filter_by(student_exam_id=student_exam_id).all()
        answer_dict = {ans.question_id: ans for ans in student_answers}
        
        earned_points = 0
        total_points = 0
        
        for question in questions:
            points = question.points or 1.0
            total_points += points
            
            student_answer = answer_dict.get(question.id)
            
            if student_answer and student_answer.selected_answer:
                is_correct = (student_answer.selected_answer.upper() == question.correct_answer.upper())
                
                if is_correct:
                    earned_points += points
                    student_answer.is_correct = True
                    student_answer.points_earned = points
                else:
                    student_answer.is_correct = False
                    student_answer.points_earned = 0
            else:
                if not student_answer:
                    student_answer = StudentAnswer(
                        student_exam_id=student_exam_id,
                        question_id=question.id,
                        selected_answer="0",
                        is_correct=False,
                        points_earned=0
                    )
                    db.session.add(student_answer)
        
        percentage = (earned_points / total_points * 100) if total_points > 0 else 0
        passing_score = exam.passing_score or 50.0
        passed = percentage >= passing_score
        
        student_exam.score = round(earned_points, 2)
        student_exam.total_points = round(total_points, 2)
        student_exam.percentage = round(percentage, 2)
        student_exam.passed = passed
        student_exam.status = 'completed'
        student_exam.completed = True
        
        if student_exam.started_at and student_exam.submitted_at:
            time_taken = (student_exam.submitted_at - student_exam.started_at).total_seconds() / 60
            student_exam.time_taken_minutes = int(time_taken)
        
        db.session.commit()
        
        return {
            'score': earned_points,
            'total_points': total_points,
            'percentage': percentage,
            'passed': passed
        }
        
    except Exception as e:
        print(f"❌ Error calculating score: {e}")
        db.session.rollback()
        return None

def assign_shuffle(student_exam):
    """Create a persistent shuffled question & option order for a new StudentExam."""
    exam = student_exam.exam
    questions = Question.query.filter_by(exam_id=exam.id).all()
    random.shuffle(questions)
    q_order = [q.id for q in questions]
    option_mapping = {}

    for q in questions:
        options = ['A', 'B', 'C', 'D']
        random.shuffle(options)
        option_mapping[str(q.id)] = options

    student_exam.question_order = json.dumps(q_order)
    student_exam.option_mapping = json.dumps(option_mapping)
    db.session.commit()
