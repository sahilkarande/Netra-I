"""
Database Models - Enhanced for Secure Exam Platform with AI Proctoring
Features:
- Unique PRN / Faculty ID fix
- OTP verification
- Shuffle tracking (question + option order)
- Enhanced proctoring and audit logs
- AI Face Detection Proctoring (NEW)
- Violation tracking (NEW)
- Calibration data storage (NEW)
"""

from backend.database import db
from flask_login import UserMixin
from datetime import datetime
import random
import secrets
import string
import json

# ===========================
# 🧑‍🎓 USER MODEL
# ===========================
class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'student', 'faculty', or 'admin'
    full_name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    prn_number = db.Column(db.String(20), unique=True, nullable=True)
    roll_id = db.Column(db.String(20), nullable=True)
    batch = db.Column(db.String(20), nullable=True)
    department = db.Column(db.String(100), nullable=True)
    course = db.Column(db.String(100), nullable=True)  # e.g., PGCP-BDA, DAC, DBDA
    gender = db.Column(db.String(20), nullable=True)
    employee_id = db.Column(db.String(50), unique=True, nullable=True)
    is_verified = db.Column(db.Boolean, default=False)
    otp = db.Column(db.String(6), nullable=True)
    otp_created_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    password_changed = db.Column(db.Boolean, default=False)  # Track if password changed
    password_changed_at = db.Column(db.DateTime, nullable=True)  # When password was changed
    password_hint = db.Column(db.String(255), nullable=True)  # Self-set reminder, never the password itself

    # Faculty display defaults: applied to every exam this faculty creates,
    # so the dashboard toggles are meaningful even before any exam exists.
    default_results_published = db.Column(db.Boolean, default=False)
    default_show_leaderboard = db.Column(db.Boolean, default=True)

    # Staff member who created this (student) account — scopes which faculty
    # may manage it. NULL for self-registered and pre-existing accounts. Plain
    # integer, not a foreign key: the relationships on this table are already
    # keyed on users.id and a self-reference would make them ambiguous.
    created_by = db.Column(db.Integer, nullable=True, index=True)

    @property
    def must_change_password(self):
        """Students logging in for the very first time must set their own password."""
        return self.role == 'student' and not self.password_changed

    # Relationships
    created_exams = db.relationship('Exam', backref='creator', lazy=True, foreign_keys='Exam.creator_id')
    student_exams = db.relationship('StudentExam', backref='student', lazy=True, foreign_keys='StudentExam.student_id')

    # In models.py → StudentExam model

    total_violations = db.Column(db.Integer, default=0)

    # ===========================
    # Helper Methods
    # ===========================
    def generate_otp(self):
        self.otp = ''.join(secrets.choice(string.digits) for _ in range(6))
        self.otp_created_at = datetime.utcnow()
        return self.otp

    def verify_otp(self, otp):
        if not self.otp or not self.otp_created_at:
            return False
        if self.otp != otp:
            return False
        if (datetime.utcnow() - self.otp_created_at).total_seconds() > 600:
            return False
        self.is_verified = True
        self.otp = None
        self.otp_created_at = None
        return True

    def set_as_student(self, data, verified=False):
        """Populate this user instance as a student."""
        self.role = "student"
        self.username = str(data.get("username", "")).strip() or None
        self.email = str(data.get("email", "")).strip().lower() or None
        self.full_name = str(data.get("full_name", "")).strip() or None

        # PRN Number: must be 12 digits
        prn_raw = str(data.get("prn_number", "")).strip()
        if prn_raw and prn_raw.isdigit() and len(prn_raw) == 12:
            self.prn_number = prn_raw
        else:
            self.prn_number = None

        # Auto-generate Roll ID
        if self.prn_number:
            self.roll_id = self.prn_number[-2:]
        else:
            self.roll_id = str(data.get("roll_id", "")).strip() or None

        self.batch = str(data.get("batch", "")).strip() or None
        self.department = str(data.get("department", "")).strip() or None
        self.course = str(data.get("course", "")).strip() or None
        self.phone = str(data.get("phone", "")).strip() or None
        self.gender = str(data.get("gender", "")).strip() or None
        self.password_hash = data.get("password_hash") or None
        self.is_verified = verified

        # Clean blank fields
        for attr in ["prn_number", "roll_id", "batch", "department", "course", "phone", "gender"]:
            val = getattr(self, attr)
            if isinstance(val, str) and val.strip() == "":
                setattr(self, attr, None)

    def __repr__(self):
        return f"<User {self.username}>"


# ===========================
# 📋 EXAM MODEL
# ===========================
class Exam(db.Model):
    __tablename__ = 'exam'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    duration_minutes = db.Column(db.Integer, nullable=False)
    passing_score = db.Column(db.Float, default=50.0)
    creator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)

    # NEW ACCESS CONTROL
    allow_all_students = db.Column(db.Boolean, default=True)
    allowed_students = db.Column(db.Text, nullable=True)  # comma-separated student IDs

    # Security settings
    allow_tab_switch = db.Column(db.Boolean, default=False)
    max_tab_switches = db.Column(db.Integer, default=3)
    randomize_questions = db.Column(db.Boolean, default=True)
    show_results_immediately = db.Column(db.Boolean, default=True)
    
    # NEW: AI Proctoring settings
    enable_proctoring = db.Column(db.Boolean, default=True)
    max_violations = db.Column(db.Integer, default=6)  # Auto-submit threshold
    exam_mode = db.Column(db.String(20), default='classroom')  # 'classroom' or 'home'
    proctor_settings = db.Column(db.Text, nullable=True, default='{}')

    # Access control & display
    access_key = db.Column(db.String(20))  # Auto-generated exam key
    show_leaderboard = db.Column(db.Boolean, default=True)
    post_exam_redirect = db.Column(db.String(20), default='results')
    status = db.Column(db.String(20), default='inactive')
    force_ended = db.Column(db.Boolean, default=False)

    # Results publication
    results_published = db.Column(db.Boolean, default=False)
    results_published_at = db.Column(db.DateTime)
    results_published_by = db.Column(db.Integer)

    # Default proctoring settings (matches create_exam.html form)
    PROCTOR_DEFAULTS = {
        'camera_enabled': True,
        'face_detection': True,
        'head_pose': False,
        'object_detection': False,
        'voice_detection': False,
        'tab_switch': True,
        'max_warnings': 25,
        'no_face_timeout_sec': 10,
        'warning_cooldown_sec': 15,
        'sensitivity': 'low',
    }

    def get_proctor_settings(self):
        """Return proctoring settings dict, filled with defaults for missing keys."""
        try:
            saved = json.loads(self.proctor_settings or '{}')
        except (json.JSONDecodeError, TypeError):
            saved = {}
        result = dict(self.PROCTOR_DEFAULTS)
        result.update(saved)
        return result

    def set_proctor_settings(self, settings_dict):
        """Store proctoring settings as JSON."""
        self.proctor_settings = json.dumps(settings_dict)

    @property
    def results_visible_to_students(self):
        """Students see scores, answer breakdowns and PDFs only once published."""
        return bool(self.results_published)

    @property
    def leaderboard_visible_to_students(self):
        """The leaderboard shows marks, so it also requires published results."""
        return bool(self.show_leaderboard) and self.results_visible_to_students

    @property
    def is_assigned(self):
        from flask_login import current_user
        if not current_user or not current_user.is_authenticated:
            return False
        if current_user.role != 'student':
            return True
        if self.allow_all_students:
            return True
        if not self.allowed_students:
            return False
        allowed_ids = [sid.strip() for sid in self.allowed_students.split(',') if sid.strip()]
        return str(current_user.id) in allowed_ids

    questions = db.relationship(
        'Question', backref='exam', lazy=True,
        cascade='all, delete, delete-orphan'
    )

    student_exams = db.relationship(
        'StudentExam', backref='exam', lazy=True,
        cascade='all, delete, delete-orphan'
    )


    # ===========================
    # Analytics Helpers
    # ===========================
    def get_average_score(self):
        completed = [se for se in self.student_exams if se.status == 'submitted']
        if not completed:
            return 0
        return sum(se.percentage or 0 for se in completed) / len(completed)

    def get_pass_rate(self):
        completed = [se for se in self.student_exams if se.status == 'submitted']
        if not completed:
            return 0
        passed = len([se for se in completed if se.passed])
        return (passed / len(completed)) * 100

    def __repr__(self):
        return f"<Exam {self.title}>"


# ===========================
# ❓ QUESTION MODEL
# ===========================
class Question(db.Model):
    __tablename__ = 'question'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id', ondelete='CASCADE'), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(500), nullable=False)
    option_b = db.Column(db.String(500), nullable=False)
    option_c = db.Column(db.String(500))
    option_d = db.Column(db.String(500))
    correct_answer = db.Column(db.String(1), nullable=False)
    points = db.Column(db.Float, default=1.0)
    order_number = db.Column(db.Integer)
    enhanced = db.Column(db.Boolean, default=False)

    answers = db.relationship('StudentAnswer', backref='question', lazy=True, cascade='all, delete-orphan')

    def get_accuracy_rate(self):
        total = len(self.answers)
        if not total:
            return 0
        correct = len([a for a in self.answers if a.is_correct])
        return (correct / total) * 100

    def __repr__(self):
        return f"<Question {self.id}>"


# ===========================
# 🧑‍🎓 STUDENT EXAM MODEL (Enhanced with Proctoring)
# ===========================
class StudentExam(db.Model):
    __tablename__ = 'student_exam'
    # One attempt per student per exam: a double-clicked Start must not split
    # answers across two rows.
    __table_args__ = (
        db.UniqueConstraint('student_id', 'exam_id', name='uq_student_exam_student_exam'),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id', ondelete='CASCADE'), nullable=False, index=True)
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
    completed = db.Column(db.Boolean, default=False)

    # Shuffle tracking (per student)
    question_order = db.Column(db.Text)    # JSON list like: "[3, 5, 1, 9]"
    option_mapping = db.Column(db.Text, nullable=True)   # JSON dict of {question_id: [A,B,C,D]}

    # NEW: AI Proctoring fields
    proctoring_enabled = db.Column(db.Boolean, default=True)
    calibration_completed = db.Column(db.Boolean, default=False)
    total_violations = db.Column(db.Integer, default=0)
    proctoring_status = db.Column(db.String(20), default='active')  # active, warning, terminated

    # Relationships
    answers = db.relationship('StudentAnswer', backref='student_exam', lazy=True, cascade='all, delete-orphan')
    activity_logs = db.relationship('ActivityLog', backref='student_exam', lazy=True, cascade='all, delete-orphan')
    calibration = db.relationship('ExamCalibration', backref='student_exam', uselist=False, cascade='all, delete-orphan')
    violations = db.relationship('ExamViolation', backref='student_exam', lazy=True, cascade='all, delete-orphan')
    no_face_count = db.Column(db.Integer, default=0)
    multiple_faces_count = db.Column(db.Integer, default=0)

    @property
    def result_visible_to_student(self):
        """Student may see this attempt's score only once the exam's results are published."""
        if self.is_invalidated:
            return False
        exam = self.exam
        return bool(exam) and exam.results_visible_to_students

    # Faculty review fields
    is_invalidated = db.Column(db.Boolean, default=False)  # True = exam not counted
    faculty_review_note = db.Column(db.Text)  # Reason for invalidation
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewed_at = db.Column(db.DateTime)

    # Helpers
    def get_question_order(self):
        try:
            return json.loads(self.question_order or "[]")
        except json.JSONDecodeError:
            return []

    def get_option_mapping(self):
        try:
            return json.loads(self.option_mapping or "{}")
        except json.JSONDecodeError:
            return {}

    def __repr__(self):
        return f"<StudentExam {self.id}>"


# ===========================
# 📝 STUDENT ANSWER MODEL
# ===========================
class StudentAnswer(db.Model):
    __tablename__ = 'student_answer'
    __table_args__ = (
        db.UniqueConstraint('student_exam_id', 'question_id', name='uq_student_answer_attempt_question'),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_exam_id = db.Column(db.Integer, db.ForeignKey('student_exam.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    selected_answer = db.Column(db.String(10))
    is_correct = db.Column(db.Boolean, default=False)
    points_earned = db.Column(db.Float, default=0.0)
    answered_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<StudentAnswer {self.id}>"


# ===========================
# 👁️ PROCTORING ACTIVITY LOG
# ===========================
class ActivityLog(db.Model):
    __tablename__ = 'activity_log'

    id = db.Column(db.Integer, primary_key=True)
    student_exam_id = db.Column(db.Integer, db.ForeignKey('student_exam.id'), nullable=False)
    activity_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    severity = db.Column(db.String(20), default='low')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ActivityLog {self.activity_type}>"


# ===========================
# 🎯 EXAM CALIBRATION (NEW)
# Store baseline face position data
# ===========================
class ExamCalibration(db.Model):
    __tablename__ = 'exam_calibration'

    id = db.Column(db.Integer, primary_key=True)
    student_exam_id = db.Column(db.Integer, db.ForeignKey('student_exam.id'), nullable=False, unique=True)
    baseline_yaw = db.Column(db.Float, nullable=False)
    baseline_pitch = db.Column(db.Float, nullable=False)
    baseline_roll = db.Column(db.Float, nullable=False)
    calibrated_at = db.Column(db.DateTime, default=datetime.utcnow)
    calibration_frames = db.Column(db.Integer, default=0)  # Number of frames used for calibration
    
    def __repr__(self):
        return f"<ExamCalibration SE:{self.student_exam_id}>"


# ===========================
# ⚠️ EXAM VIOLATION (NEW)
# Track all proctoring violations
# ===========================
class ExamViolation(db.Model):
    __tablename__ = 'exam_violation'

    id = db.Column(db.Integer, primary_key=True)
    student_exam_id = db.Column(db.Integer, db.ForeignKey('student_exam.id'), nullable=False)
    violation_type = db.Column(db.String(50), nullable=False)  # NO_FACE, MULTIPLE_FACES, LOOKING_AWAY, etc.
    severity = db.Column(db.String(20), nullable=False)  # low, medium, high
    message = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    evidence_path = db.Column(db.String(500))  # Path to violation screenshot
    
    # Technical details (optional)
    yaw = db.Column(db.Float)
    pitch = db.Column(db.Float)
    roll = db.Column(db.Float)
    deviation_yaw = db.Column(db.Float)
    deviation_pitch = db.Column(db.Float)
    deviation_roll = db.Column(db.Float)
    faces_detected = db.Column(db.Integer)

    def __repr__(self):
        return f"<ExamViolation {self.violation_type} at {self.timestamp}>"


# ===========================
# 🙂 FACE ENROLLMENT (face login)
# ===========================
class FaceEnrollment(db.Model):
    """A user's enrolled face, stored as descriptor vectors — never as images.

    Each sample is a 128-dimension float32 vector produced by face-api.js in the
    browser (512 bytes). Several samples are kept so matching tolerates changes
    in lighting, glasses and angle. The raw frame is never transmitted or stored.

    A face vector is a credential that cannot be rotated, so this is a
    convenience factor alongside the password, never a replacement for it.
    """
    __tablename__ = 'face_enrollment'

    DESCRIPTOR_DIM = 128
    BYTES_PER_SAMPLE = DESCRIPTOR_DIM * 4      # float32

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True, index=True)

    # N samples packed back-to-back as little-endian float32
    descriptors = db.Column(db.LargeBinary, nullable=False)
    sample_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    success_count = db.Column(db.Integer, default=0)
    fail_count = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)

    # Enrolment is one-shot: once locked, the student cannot replace their own
    # face. Only faculty/admin may clear the lock, and that is audited.
    is_locked = db.Column(db.Boolean, default=True, nullable=False)
    enrolled_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reset_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reset_at = db.Column(db.DateTime, nullable=True)

    # Liveness evidence recorded at enrolment time
    liveness_score = db.Column(db.Float, nullable=True)     # mean spoof score, lower is better
    liveness_method = db.Column(db.String(40), nullable=True)

    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('face_enrollment', uselist=False))

    def __repr__(self):
        return f"<FaceEnrollment user={self.user_id} samples={self.sample_count}>"


class FaceLoginAttempt(db.Model):
    """Audit trail for face logins. Biometric auth needs a reviewable history."""
    __tablename__ = 'face_login_attempt'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    identifier = db.Column(db.String(100))       # what was typed in the login box
    success = db.Column(db.Boolean, default=False)
    distance = db.Column(db.Float, nullable=True)
    reason = db.Column(db.String(100), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Liveness outcome — a rejected attempt records WHY it was judged a spoof
    liveness_passed = db.Column(db.Boolean, nullable=True)
    spoof_score = db.Column(db.Float, nullable=True)
    liveness_detail = db.Column(db.Text, nullable=True)     # JSON of per-frame features
    stage = db.Column(db.String(20), nullable=True)         # enroll | login | exam

    def __repr__(self):
        return f"<FaceLoginAttempt {self.identifier} success={self.success}>"


class FaceAuditLog(db.Model):
    """Who changed whose face template, when, and why.

    Face data is a credential that cannot be rotated, so every create, reset and
    delete is recorded permanently — including which staff member authorised it.
    """
    __tablename__ = 'face_audit_log'

    id = db.Column(db.Integer, primary_key=True)
    subject_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    actor_role = db.Column(db.String(20), nullable=True)

    action = db.Column(db.String(30), nullable=False)   # enrolled | reset | deleted | relocked
    reason = db.Column(db.Text, nullable=True)
    sample_count = db.Column(db.Integer, nullable=True)
    liveness_score = db.Column(db.Float, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    subject = db.relationship('User', foreign_keys=[subject_user_id])
    actor = db.relationship('User', foreign_keys=[actor_user_id])

    def __repr__(self):
        return f"<FaceAuditLog {self.action} subject={self.subject_user_id}>"


# ===========================
# 🌀 SHUFFLE ASSIGNMENT HELPER
# ===========================
def assign_shuffle(student_exam):
    """Create a persistent shuffled question & option order for a new StudentExam."""
    import random, json

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