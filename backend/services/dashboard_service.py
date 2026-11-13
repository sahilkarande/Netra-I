"""
Read-only summaries for the faculty dashboard.

The dashboard leads with what needs a teacher's attention now, then the term's
numbers, then the exams themselves. Everything here is derived from existing
rows; nothing is written.
"""

from datetime import datetime
from typing import Dict, Iterable, List

from flask import url_for
from sqlalchemy import func

from backend.database import db
from backend.models import Question, StudentExam

# Same threshold the analytics page uses to call an attempt "flagged".
FLAG_THRESHOLD = 3

SCORE_BANDS = [(0, 20), (21, 40), (41, 60), (61, 80), (81, 100)]

STATUS_LABELS = {
    'live': 'Live',
    'open': 'Open',
    'scheduled': 'Scheduled',
    'closed': 'Closed',
    'inactive': 'Inactive',
}


def exam_status(exam, in_progress: int, now: datetime = None) -> str:
    """One word for where an exam stands right now."""
    now = now or datetime.utcnow()
    if not exam.is_active:
        return 'inactive'
    if exam.force_ended or (exam.end_time and exam.end_time < now):
        return 'closed'
    if in_progress:
        return 'live'
    if exam.start_time and exam.start_time > now:
        return 'scheduled'
    return 'open'


def _per_exam_counts(exam_ids: List[int]) -> Dict[int, dict]:
    counts = {eid: {'attempts': 0, 'in_progress': 0, 'submitted': 0, 'avg': None,
                    'flagged': 0, 'questions': 0} for eid in exam_ids}
    if not exam_ids:
        return counts

    submitted = db.or_(StudentExam.status.in_(('submitted', 'completed')),
                       StudentExam.completed == True)  # noqa: E712
    rows = db.session.query(
        StudentExam.exam_id,
        func.count(StudentExam.id),
        func.sum(db.case((StudentExam.status == 'started', 1), else_=0)),
        func.sum(db.case((submitted, 1), else_=0)),
        func.avg(db.case((submitted, StudentExam.percentage))),
        func.sum(db.case((db.and_(
            StudentExam.total_violations >= FLAG_THRESHOLD,
            db.or_(StudentExam.is_invalidated == False, StudentExam.is_invalidated.is_(None)),  # noqa: E712
            StudentExam.reviewed_at.is_(None),
        ), 1), else_=0)),
    ).filter(StudentExam.exam_id.in_(exam_ids)).group_by(StudentExam.exam_id)

    for exam_id, attempts, in_progress, done, avg, flagged in rows:
        counts[exam_id].update(attempts=attempts, in_progress=int(in_progress or 0),
                               submitted=int(done or 0),
                               avg=round(avg, 1) if avg is not None else None,
                               flagged=int(flagged or 0))

    for exam_id, n in (db.session.query(Question.exam_id, func.count(Question.id))
                       .filter(Question.exam_id.in_(exam_ids)).group_by(Question.exam_id)):
        counts[exam_id]['questions'] = n
    return counts


def exam_rows(exams: Iterable, now: datetime = None) -> List[dict]:
    """Exams with the figures the dashboard table shows, newest first."""
    exams = list(exams)
    counts = _per_exam_counts([e.id for e in exams])
    rows = []
    for exam in exams:
        c = counts[exam.id]
        status = exam_status(exam, c['in_progress'], now)
        rows.append({'exam': exam, 'status': status, 'status_label': STATUS_LABELS[status], **c})
    return rows


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def attention_items(rows: List[dict], limit: int = 3) -> List[dict]:
    """What needs action now, most urgent first.

    Live exams get one item each (they need watching individually); flagged
    attempts and unpublished results are each rolled into a single item that
    points at the exam with the most to deal with.
    """
    items = []
    for r in rows:
        if r['status'] == 'live':
            items.append({
                'tone': 'danger', 'icon': 'broadcast',
                'title': f"Live: {r['exam'].title}",
                'detail': f"{_plural(r['in_progress'], 'student')} attempting now",
                'href': url_for('faculty.view_exam', exam_id=r['exam'].id),
                'action': 'Monitor',
            })

    flagged = sorted((r for r in rows if r['flagged']), key=lambda r: -r['flagged'])
    if flagged:
        total = sum(r['flagged'] for r in flagged)
        items.append({
            'tone': 'warning', 'icon': 'flag',
            'title': f"{_plural(total, 'attempt')} flagged for review",
            'detail': ', '.join(f"{r['exam'].title} ({r['flagged']})" for r in flagged),
            'href': url_for('faculty.exam_analytics', exam_id=flagged[0]['exam'].id),
            'action': 'Review',
        })

    unpublished = sorted((r for r in rows if r['submitted'] and not r['exam'].results_published),
                         key=lambda r: -r['submitted'])
    if unpublished:
        items.append({
            'tone': 'accent', 'icon': 'eye-slash',
            'title': ('Results not published' if len(unpublished) == 1
                      else f"Results not published for {len(unpublished)} exams"),
            'detail': ', '.join(f"{r['exam'].title} ({r['submitted']} submitted)" for r in unpublished),
            'href': url_for('faculty.view_exam', exam_id=unpublished[0]['exam'].id),
            'action': 'Open',
        })
    return items[:limit]


def score_distribution(exam_ids: List[int]) -> List[dict]:
    """Submitted attempts per 20-point score band, for the dashboard chart."""
    bands = [{'label': f'{lo}–{hi}', 'low': lo, 'high': hi, 'count': 0} for lo, hi in SCORE_BANDS]
    if not exam_ids:
        return bands
    pcts = (db.session.query(StudentExam.percentage)
            .filter(StudentExam.exam_id.in_(exam_ids),
                    db.or_(StudentExam.status.in_(('submitted', 'completed')),
                           StudentExam.completed == True))  # noqa: E712
            .all())
    for (pct,) in pcts:
        value = max(0.0, min(100.0, float(pct or 0)))
        for band in bands:
            if value <= band['high'] or band is bands[-1]:
                band['count'] += 1
                break
    peak = max((b['count'] for b in bands), default=0)
    for band in bands:
        band['share'] = (band['count'] / peak) if peak else 0
    return bands
