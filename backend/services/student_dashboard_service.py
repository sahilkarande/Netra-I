"""
Read-only summaries for the student dashboard.

Each exam a student can see gets one state that says what they can do with it
right now, so the page never offers an action the server will refuse (for
example "Retake" on an exam that can only be sat once).
"""

from datetime import datetime
from typing import Dict, List, Optional

_EPOCH = datetime(1970, 1, 2)

# state -> (badge label, badge tone)
STATES = {
    'in_progress': ('In progress', 'warning'),
    'open': ('Open', 'success'),
    'upcoming': ('Upcoming', 'accent'),
    'closed': ('Closed', 'neutral'),
    'awaiting': ('Submitted', 'neutral'),
    'released': ('Result out', 'success'),
    'invalidated': ('Invalidated', 'danger'),
}

# Exams the student can act on first, then the ones they are waiting for
ORDER = ['in_progress', 'open', 'upcoming', 'released', 'awaiting', 'invalidated', 'closed']


def _finished(attempt) -> bool:
    return bool(attempt and (attempt.completed or attempt.status in ('submitted', 'completed')))


def exam_state(exam, attempt, now: Optional[datetime] = None) -> str:
    """Where this exam stands for this student."""
    now = now or datetime.utcnow()
    if _finished(attempt):
        if attempt.is_invalidated:
            return 'invalidated'
        return 'released' if attempt.result_visible_to_student else 'awaiting'
    if attempt and attempt.status == 'started':
        return 'in_progress'
    if not exam.is_active or exam.force_ended or (exam.end_time and now > exam.end_time):
        return 'closed'
    if exam.start_time and now < exam.start_time:
        return 'upcoming'
    return 'open'


def exam_rows(exams, attempts, now: Optional[datetime] = None) -> List[Dict]:
    """One row per exam assigned to the student, most actionable first."""
    now = now or datetime.utcnow()
    by_exam = {a.exam_id: a for a in attempts}
    rows = []
    for exam in exams:
        attempt = by_exam.get(exam.id)
        state = exam_state(exam, attempt, now)
        label, tone = STATES[state]
        rows.append({
            'exam': exam,
            'attempt': attempt,
            'state': state,
            'label': label,
            'tone': tone,
            'questions': len(exam.questions),
            'score': attempt.percentage if state == 'released' else None,
            'passed': attempt.passed if state == 'released' else None,
        })
    rows.sort(key=lambda r: (ORDER.index(r['state']),
                             -(r['exam'].created_at or _EPOCH).timestamp()))
    return rows


def score_trend(published_attempts) -> List[Dict]:
    """Released scores in the order they were sat, for the trend chart."""
    rows = [a for a in published_attempts if a.percentage is not None]
    rows.sort(key=lambda a: a.submitted_at or _EPOCH)
    return [{
        'title': a.exam.title if a.exam else 'Exam',
        'score': round(a.percentage or 0, 1),
        'passed': bool(a.passed),
        'pass_mark': (a.exam.passing_score if a.exam and a.exam.passing_score is not None else 50),
        'date': a.submitted_at,
    } for a in rows]
