"""What is kept, for how long, and how a deleted exam comes back (audit P2-10).

Evidence images
    Publishing results used to delete every proctoring image on the spot, so a
    dispute raised the day after results came out had nothing to look at. Now
    images are kept for EVIDENCE_RETENTION_DAYS (default 30) after results are
    published, then deleted by purge_expired(). Faculty can still delete single
    images at any time from Analytics.

Deleted exams
    Deleting an exam removes it and every attempt from the database, as before,
    but first the whole exam (questions, attempts, answers, flags, activity) is
    written to instance/deleted_exams/ as JSON. An administrator can restore it
    from All exams for DELETED_EXAM_RETENTION_DAYS (default 30); after that the
    archive and its evidence images are deleted for good.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import DateTime, Date, inspect as sa_inspect

from backend.database import db
from backend.models import (
    ActivityLog, Exam, ExamCalibration, ExamViolation, Question, StudentAnswer,
    StudentExam, User,
)

log = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
EVIDENCE_ROOT = os.path.join(PROJECT_ROOT, 'frontend', 'static', 'violations')
ARCHIVE_VERSION = 1


def evidence_days():
    return int(os.getenv('EVIDENCE_RETENTION_DAYS', '30'))


def archive_days():
    return int(os.getenv('DELETED_EXAM_RETENTION_DAYS', '30'))


# ── Evidence files ─────────────────────────────────────────────────────────

def evidence_file(path):
    """Absolute path of an evidence image, only if it really is one.

    The stored path is never trusted to point outside the evidence folder.
    """
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(PROJECT_ROOT, path)
    path = os.path.realpath(path)
    root = os.path.realpath(EVIDENCE_ROOT)
    if os.path.commonpath([path, root]) != root or not os.path.isfile(path):
        return None
    return path


def _remove_file(path):
    real = evidence_file(path)
    if not real:
        return False
    try:
        os.remove(real)
    except OSError:
        log.warning('Could not delete evidence file %s', real, exc_info=True)
        return False
    # Tidy now-empty per-attempt folders, never the root itself
    parent = os.path.dirname(real)
    root = os.path.realpath(EVIDENCE_ROOT)
    while parent != root and parent.startswith(root):
        try:
            os.rmdir(parent)            # only succeeds when empty
        except OSError:
            break
        parent = os.path.dirname(parent)
    return True


def evidence_delete_after(exam):
    """When this exam's images will be deleted, or None while results are unpublished."""
    if not exam.results_published or not exam.results_published_at:
        return None
    return exam.results_published_at + timedelta(days=evidence_days())


def purge_exam_evidence(exam_ids):
    """Delete every evidence image of these exams. Returns how many files went."""
    if not exam_ids:
        return 0
    removed = 0
    rows = (ExamViolation.query.join(StudentExam, ExamViolation.student_exam_id == StudentExam.id)
            .filter(StudentExam.exam_id.in_(list(exam_ids)), ExamViolation.evidence_path.isnot(None))
            .all())
    for v in rows:
        if _remove_file(v.evidence_path):
            removed += 1
        v.evidence_path = None
    db.session.commit()
    return removed


# ── Deleted-exam archives ──────────────────────────────────────────────────

def archive_dir():
    path = os.path.join(current_app.instance_path, 'deleted_exams')
    os.makedirs(path, exist_ok=True)
    return path


def _row(obj):
    out = {}
    for col in sa_inspect(obj).mapper.column_attrs:
        value = getattr(obj, col.key)
        if isinstance(value, datetime):
            value = value.isoformat()
        out[col.key] = value
    return out


def archive_exam(exam, actor):
    """Write the exam and everything under it to a JSON file; returns its name."""
    attempts = StudentExam.query.filter_by(exam_id=exam.id).all()
    attempt_ids = [a.id for a in attempts]

    def by_attempt(model):
        if not attempt_ids:
            return []
        return [_row(r) for r in model.query.filter(model.student_exam_id.in_(attempt_ids)).all()]

    payload = {
        'version': ARCHIVE_VERSION,
        'deleted_at': datetime.utcnow().isoformat(),
        'deleted_by': actor.id if actor else None,
        'deleted_by_name': (actor.full_name or actor.username) if actor else None,
        'exam': _row(exam),
        'questions': [_row(q) for q in Question.query.filter_by(exam_id=exam.id).all()],
        'attempts': [_row(a) for a in attempts],
        'answers': by_attempt(StudentAnswer),
        'activity': by_attempt(ActivityLog),
        'violations': by_attempt(ExamViolation),
        'calibrations': by_attempt(ExamCalibration),
    }
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
    name = f'exam-{exam.id}-{stamp}.json'
    path = os.path.join(archive_dir(), name)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)               # never leave a half-written archive
    log.info('Archived exam %s (%r) to %s before deletion', exam.id, exam.title, name)
    return name


_NAME = re.compile(r'^exam-\d+-\d{8}T\d{6}\.json$')


def _archive_path(name):
    if not name or not _NAME.match(name):
        return None
    path = os.path.join(archive_dir(), name)
    return path if os.path.isfile(path) else None


def list_archives():
    """Newest first: [{name, title, deleted_at, deleted_by_name, attempts, questions, expires_at}]."""
    out = []
    for name in os.listdir(archive_dir()):
        path = _archive_path(name)
        if not path:
            continue
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            log.warning('Unreadable exam archive %s', name)
            continue
        deleted_at = datetime.fromisoformat(data['deleted_at'])
        out.append({
            'name': name,
            'title': data['exam'].get('title') or 'Untitled exam',
            'deleted_at': deleted_at,
            'deleted_by_name': data.get('deleted_by_name'),
            'attempts': len(data.get('attempts', [])),
            'questions': len(data.get('questions', [])),
            'expires_at': deleted_at + timedelta(days=archive_days()),
        })
    out.sort(key=lambda a: a['deleted_at'], reverse=True)
    return out


def _coerce(model, data, drop=('id',)):
    """Archive dict -> constructor kwargs: known columns only, dates parsed."""
    cols = {c.key: c for c in sa_inspect(model).mapper.column_attrs}
    kwargs = {}
    for key, value in data.items():
        if key in drop or key not in cols:
            continue
        col_type = cols[key].columns[0].type
        if value is not None and isinstance(col_type, (DateTime, Date)) and isinstance(value, str):
            value = datetime.fromisoformat(value)
        kwargs[key] = value
    return kwargs


def _remap_json(text, id_map, keys=False):
    """Rewrite question ids inside a stored JSON list or dict."""
    if not text:
        return text
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, list):
        data = [id_map.get(int(x), x) if str(x).isdigit() else x for x in data]
    elif isinstance(data, dict):
        data = {str(id_map.get(int(k), k)) if str(k).isdigit() else k: v for k, v in data.items()}
    return json.dumps(data)


def restore_archive(name, actor):
    """Recreate an archived exam with new ids. Returns (exam, skipped_attempts).

    Attempts by students who no longer exist are skipped. If the exam's owner
    was removed, the restoring administrator becomes the owner. The archive is
    deleted once the restore has been committed.
    """
    path = _archive_path(name)
    if not path:
        raise FileNotFoundError(name)
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)

    existing_users = {uid for (uid,) in db.session.query(User.id)}

    exam_kwargs = _coerce(Exam, data['exam'])
    if exam_kwargs.get('creator_id') not in existing_users:
        exam_kwargs['creator_id'] = actor.id
    if exam_kwargs.get('results_published_by') not in existing_users:
        exam_kwargs['results_published_by'] = None
    exam = Exam(**exam_kwargs)
    db.session.add(exam)
    db.session.flush()

    question_map = {}
    for q in data.get('questions', []):
        new = Question(**dict(_coerce(Question, q), exam_id=exam.id))
        db.session.add(new)
        db.session.flush()
        question_map[q['id']] = new.id

    attempt_map, skipped = {}, 0
    for a in data.get('attempts', []):
        if a.get('student_id') not in existing_users:
            skipped += 1
            continue
        kwargs = dict(_coerce(StudentExam, a), exam_id=exam.id)
        kwargs['device_token'] = None            # nobody's browser holds the restored attempt
        if kwargs.get('reviewed_by') not in existing_users:
            kwargs['reviewed_by'] = None
        kwargs['question_order'] = _remap_json(kwargs.get('question_order'), question_map)
        kwargs['option_mapping'] = _remap_json(kwargs.get('option_mapping'), question_map)
        new = StudentExam(**kwargs)
        db.session.add(new)
        db.session.flush()
        attempt_map[a['id']] = new.id

    def children(model, key, extra=None):
        for row in data.get(key, []):
            se = attempt_map.get(row.get('student_exam_id'))
            if se is None:
                continue
            kwargs = dict(_coerce(model, row), student_exam_id=se)
            if extra:
                kwargs = extra(row, kwargs)
                if kwargs is None:
                    continue
            db.session.add(model(**kwargs))

    def answer_fix(row, kwargs):
        qid = question_map.get(row.get('question_id'))
        if qid is None:
            return None
        kwargs['question_id'] = qid
        return kwargs

    children(StudentAnswer, 'answers', answer_fix)
    children(ActivityLog, 'activity')
    children(ExamViolation, 'violations')
    children(ExamCalibration, 'calibrations')

    db.session.commit()
    os.remove(path)
    log.info('Restored exam %r from %s as exam %s (%d attempts skipped)',
             exam.title, name, exam.id, skipped)
    return exam, skipped


# ── The sweep ──────────────────────────────────────────────────────────────

def purge_expired(now=None):
    """Delete evidence past its retention and archives past theirs.

    Returns {'evidence': files_removed, 'archives': archives_removed}.
    """
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=evidence_days())
    due = [e.id for e in Exam.query.filter(Exam.results_published.is_(True),
                                           Exam.results_published_at.isnot(None),
                                           Exam.results_published_at < cutoff)]
    removed = purge_exam_evidence(due)

    archives = 0
    for item in list_archives():
        if item['expires_at'] < now:
            path = _archive_path(item['name'])
            try:
                with open(path, encoding='utf-8') as fh:
                    for v in json.load(fh).get('violations', []):
                        if _remove_file(v.get('evidence_path')):
                            removed += 1
                os.remove(path)
                archives += 1
            except (OSError, ValueError):
                log.warning('Could not purge exam archive %s', item['name'], exc_info=True)

    if removed or archives:
        log.info('Retention sweep: %d evidence image(s) and %d exam archive(s) deleted',
                 removed, archives)
    return {'evidence': removed, 'archives': archives}


def start_sweeper(app, socketio, interval_sec=6 * 3600):
    """Run purge_expired() at start-up and then every `interval_sec` seconds."""
    if getattr(app, '_retention_sweeper_started', False):
        return
    app._retention_sweeper_started = True

    def loop():
        while True:
            try:
                with app.app_context():
                    purge_expired()
            except Exception:
                log.exception('Retention sweep failed')
            socketio.sleep(interval_sec)

    socketio.start_background_task(loop)
