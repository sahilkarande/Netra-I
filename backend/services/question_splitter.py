"""
Question bank splitter — a stateless file-in / files-out utility.

What it is for
──────────────
A faculty has one file of (say) 120 questions and wants 4 papers that share no
questions. They upload the file here, choose how many sets, and download the
split — one file per set, in exactly the same format the exam uploader already
accepts. From there the normal flow is unchanged: create an exam, upload a set
file to it.

Deliberately stateless
──────────────────────
Nothing here touches the database. No exam is created, no question row is
written. That keeps this a tool rather than a second way of building exams, and
it means a faculty can split, inspect the files, and throw them away with no
side effects.

Distribution
────────────
``unique`` (default) guarantees no question appears in two sets — the whole
point of having sets. ``shared`` is the fallback when the bank is too small for
that, and it is opt-in so nobody gets silent overlap.

With ``balance_marks`` on, each question is dealt to whichever set currently has
the fewest marks, so sets come out worth the same even when questions carry
different point values. Without it, one paper can quietly be worth more than
another — a fairness bug that only surfaces after results are published.
"""

import csv
import io
import json
import random
import string
from typing import Dict, List, Optional, Tuple

MAX_SETS = 26
MAX_QUESTIONS = 5000           # generous upper bound; guards against junk uploads

# Canonical field names written out, matching what upload_questions() reads back.
OUT_FIELDS = ['question', 'option_a', 'option_b', 'option_c', 'option_d',
              'correct_answer', 'points']


def set_label_for(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    letters = string.ascii_uppercase
    if index < len(letters):
        return letters[index]
    first, second = divmod(index - len(letters), len(letters))
    return letters[first] + letters[second]


# ── Parsing ─────────────────────────────────────────────────────────────────

def _first(row: dict, *names):
    """First non-empty value among the given column names, case-insensitively."""
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None and str(value).strip() != '' and str(value).lower() != 'nan':
            return str(value).strip()
    return None


def parse_questions(filename: str, raw: bytes) -> Tuple[bool, str, List[dict]]:
    """Read a CSV / JSON / Excel bank into normalised question dicts.

    Accepts the same spread of column spellings the exam uploader does, so a
    file that works there works here.
    """
    name = (filename or '').lower()

    try:
        if name.endswith('.json'):
            data = json.loads(raw.decode('utf-8-sig'))
            if isinstance(data, dict):
                data = data.get('questions') or data.get('data') or []
            if not isinstance(data, list):
                return False, 'JSON must be a list of question objects.', []
            rows = data

        elif name.endswith(('.xlsx', '.xls')):
            import pandas as pd
            df = pd.read_excel(io.BytesIO(raw), dtype=str)
            rows = df.to_dict(orient='records')

        elif name.endswith(('.csv', '.txt')):
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('latin-1')
            rows = list(csv.DictReader(io.StringIO(text, newline=None)))

        else:
            return False, 'Upload a CSV, JSON or Excel file.', []

    except Exception as e:
        return False, f'Could not read the file: {e}', []

    questions, skipped = [], 0

    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue

        text = _first(row, 'question', 'question_text', 'text', 'Question')
        if not text:
            skipped += 1
            continue

        a = _first(row, 'a', 'option_a', 'option1', 'Option A', 'optiona')
        b = _first(row, 'b', 'option_b', 'option2', 'Option B', 'optionb')
        c = _first(row, 'c', 'option_c', 'option3', 'Option C', 'optionc')
        d = _first(row, 'd', 'option_d', 'option4', 'Option D', 'optiond')

        if not a or not b:
            skipped += 1               # need at least two choices to be a question
            continue

        answer = (_first(row, 'answer', 'correct_answer', 'correct', 'Answer')
                  or 'A').strip().upper()[:1]
        if answer not in ('A', 'B', 'C', 'D'):
            answer = 'A'

        try:
            points = float(_first(row, 'points', 'score', 'marks') or 1.0)
        except (TypeError, ValueError):
            points = 1.0

        questions.append({
            'question': text,
            'option_a': a,
            'option_b': b,
            'option_c': c or '',
            'option_d': d or '',
            'correct_answer': answer,
            'points': points,
        })

        if len(questions) >= MAX_QUESTIONS:
            break

    if not questions:
        return False, ('No usable questions found. Each row needs question text and at '
                       'least options A and B.'), []

    note = f' ({skipped} row(s) skipped)' if skipped else ''
    return True, f'Read {len(questions)} questions{note}.', questions


def _read_rows(filename: str, raw: bytes):
    """Raw dict rows from a CSV / JSON / Excel upload, or raise ValueError."""
    name = (filename or '').lower()
    if name.endswith('.json'):
        data = json.loads(raw.decode('utf-8-sig'))
        if isinstance(data, dict):
            data = data.get('questions') or data.get('data') or []
        if not isinstance(data, list):
            raise ValueError('JSON must be a list of question objects.')
        return data
    if name.endswith(('.xlsx', '.xls')):
        import pandas as pd
        return pd.read_excel(io.BytesIO(raw), dtype=str).to_dict(orient='records')
    if name.endswith(('.csv', '.txt')):
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('latin-1')
        return list(csv.DictReader(io.StringIO(text, newline=None)))
    raise ValueError('Upload a CSV, JSON or Excel file.')


def validate_questions(filename: str, raw: bytes) -> Tuple[List[dict], List[str]]:
    """Strict reader for questions that go straight onto an exam paper.

    Unlike parse_questions() (which tolerates a messy bank before splitting),
    nothing here is guessed: a missing or invalid answer key, an answer that
    points at an empty option, or bad marks is reported with its row number
    instead of being silently turned into something else. Missing options C/D
    are stored empty, never as the text "None" or "nan".

    Returns (questions, errors). Callers should refuse the whole file when
    errors is non-empty, so a paper is never half-uploaded.
    """
    try:
        rows = _read_rows(filename, raw)
    except ValueError as e:
        return [], [str(e)]
    except Exception:
        return [], ['Could not read the file. Check it is a valid CSV, JSON or Excel file.']

    questions, errors = [], []
    first_data_row = 2 if (filename or '').lower().endswith(('.csv', '.txt', '.xlsx', '.xls')) else 1

    for offset, row in enumerate(rows):
        n = offset + first_data_row
        if not isinstance(row, dict):
            errors.append(f'Row {n}: not a question object.')
            continue
        text = _first(row, 'question', 'question_text', 'text', 'Question')
        if not text:
            if any(_first(row, k) for k in row.keys() if k):
                errors.append(f'Row {n}: question text is missing.')
            continue                       # fully blank rows are ignored

        options = {
            'A': _first(row, 'a', 'option_a', 'option1', 'Option A', 'optiona'),
            'B': _first(row, 'b', 'option_b', 'option2', 'Option B', 'optionb'),
            'C': _first(row, 'c', 'option_c', 'option3', 'Option C', 'optionc'),
            'D': _first(row, 'd', 'option_d', 'option4', 'Option D', 'optiond'),
        }
        if not options['A'] or not options['B']:
            errors.append(f'Row {n}: options A and B are required.')
            continue

        answer = (_first(row, 'answer', 'correct_answer', 'correct', 'Answer') or '').strip().upper()
        if not answer:
            errors.append(f'Row {n}: correct answer is missing (use A, B, C or D).')
            continue
        if answer not in options:
            errors.append(f'Row {n}: correct answer "{answer[:10]}" is not A, B, C or D.')
            continue
        if not options[answer]:
            errors.append(f'Row {n}: correct answer is {answer}, but option {answer} is empty.')
            continue

        raw_points = _first(row, 'points', 'score', 'marks')
        try:
            points = float(raw_points) if raw_points is not None else 1.0
        except (TypeError, ValueError):
            errors.append(f'Row {n}: points "{str(raw_points)[:10]}" is not a number.')
            continue
        if not 0 < points <= 1000:
            errors.append(f'Row {n}: points must be greater than 0.')
            continue

        questions.append({
            'question': text,
            'option_a': options['A'],
            'option_b': options['B'],
            'option_c': options['C'] or None,
            'option_d': options['D'] or None,
            'correct_answer': answer,
            'points': points,
        })
        if len(questions) >= MAX_QUESTIONS:
            break

    if not questions and not errors:
        errors.append('No questions found in the file.')
    return questions, errors


# ── Splitting ───────────────────────────────────────────────────────────────

def split_questions(questions: List[dict], num_sets: int,
                    per_set: Optional[int] = None,
                    strategy: str = 'unique',
                    balance_marks: bool = True,
                    seed: Optional[int] = None) -> Tuple[bool, str, Optional[dict]]:
    """Deal a bank into N sets. Returns (ok, message, result)."""
    total = len(questions)

    if total == 0:
        return False, 'There are no questions to split.', None
    if num_sets < 2:
        return False, 'Split into at least 2 sets.', None
    if num_sets > MAX_SETS:
        return False, f'At most {MAX_SETS} sets at a time.', None

    if not per_set:
        per_set = total // num_sets

    if per_set < 1:
        return False, (f'{total} questions cannot make {num_sets} sets — there are fewer '
                       'questions than sets.'), None

    needed = per_set * num_sets

    if strategy == 'unique' and needed > total:
        return False, (f'{num_sets} sets of {per_set} needs {needed} questions, but the file '
                       f'has {total}. Use {total // num_sets} per set, fewer sets, or allow '
                       'questions to be shared.'), None

    rng = random.Random(seed) if seed is not None else random.Random()
    pool = list(questions)
    rng.shuffle(pool)

    buckets: List[List[dict]] = [[] for _ in range(num_sets)]
    totals = [0.0] * num_sets

    if strategy == 'unique':
        selected = pool[:needed]

        if balance_marks:
            # Heaviest question first, each to the lightest set so far.
            for q in sorted(selected, key=lambda x: -x['points']):
                open_sets = [i for i in range(num_sets) if len(buckets[i]) < per_set]
                target = min(open_sets, key=lambda i: totals[i])
                buckets[target].append(q)
                totals[target] += q['points']
        else:
            for i in range(num_sets):
                buckets[i] = selected[i * per_set:(i + 1) * per_set]
                totals[i] = sum(q['points'] for q in buckets[i])

        leftover = total - needed
    else:
        for i in range(num_sets):
            picked = rng.sample(pool, min(per_set, total))
            buckets[i] = picked
            totals[i] = sum(q['points'] for q in picked)
        leftover = 0

    # Shuffle within each set too, so balancing does not leave them ordered by marks.
    for bucket in buckets:
        rng.shuffle(bucket)

    return True, f'Split into {num_sets} sets of {per_set}.', {
        'num_sets': num_sets,
        'per_set': per_set,
        'strategy': strategy,
        'balance_marks': balance_marks,
        'bank_total': total,
        'leftover': max(0, leftover),
        'sets': [{
            'label': set_label_for(i),
            'questions': buckets[i],
            'count': len(buckets[i]),
            'total_marks': round(totals[i], 2),
        } for i in range(num_sets)],
    }


# ── Serialising ─────────────────────────────────────────────────────────────

def set_to_json(one_set: dict) -> str:
    return json.dumps(one_set['questions'], indent=2, ensure_ascii=False)


def set_to_csv(one_set: dict) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=OUT_FIELDS, lineterminator='\n')
    writer.writeheader()
    for q in one_set['questions']:
        writer.writerow({k: q.get(k, '') for k in OUT_FIELDS})
    return out.getvalue()


def build_zip(result: dict, base_name: str, fmt: str = 'json') -> bytes:
    """Bundle every set into one archive, plus a short summary."""
    import zipfile

    safe = ''.join(ch for ch in (base_name or 'question_bank')
                   if ch.isalnum() or ch in ' _-').strip().replace(' ', '_') or 'question_bank'

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for one_set in result['sets']:
            if fmt == 'csv':
                archive.writestr(f"{safe}_Set_{one_set['label']}.csv", set_to_csv(one_set))
            else:
                archive.writestr(f"{safe}_Set_{one_set['label']}.json", set_to_json(one_set))

        summary = [
            f"Question bank split — {safe}",
            f"Source questions : {result['bank_total']}",
            f"Sets             : {result['num_sets']}",
            f"Questions per set: {result['per_set']}",
            f"Strategy         : {result['strategy']}"
            + (" (no question repeats across sets)" if result['strategy'] == 'unique'
               else " (questions may appear in more than one set)"),
            f"Left over        : {result['leftover']}",
            '',
        ]
        for one_set in result['sets']:
            summary.append(f"  Set {one_set['label']}: {one_set['count']} questions, "
                           f"{one_set['total_marks']} marks")
        archive.writestr('SUMMARY.txt', '\n'.join(summary) + '\n')

    return buffer.getvalue()
