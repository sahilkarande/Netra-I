"""
One-off repair for exams created before the proctoring-settings fix.

The create-exam form posts the whole proctoring block as `proctor_settings_json`,
but the route used to ignore it and rebuild the settings from field names that the
form never sends. Every exam therefore got `camera_enabled: false` saved, and the
student's pre-exam screen showed "Camera monitoring off." no matter what the
faculty had ticked.

This script turns the camera back on for exams that were hit by that bug. It only
touches an exam when BOTH of these hold, which is the fingerprint of the bug:

  * proctor_settings says camera_enabled is false, and
  * the exam.enable_proctoring column is still true (the old route never wrote it)

Run from the project root:

    python scripts/repair_proctor_settings.py            # show what would change
    python scripts/repair_proctor_settings.py --apply    # write the changes
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from backend.database import db  # noqa: E402
from backend.models import Exam  # noqa: E402


def main():
    apply_changes = '--apply' in sys.argv

    app = create_app()
    with app.app_context():
        exams = Exam.query.all()
        repaired = []

        for exam in exams:
            try:
                saved = json.loads(exam.proctor_settings or '{}')
            except (ValueError, TypeError):
                saved = {}

            camera_off = saved.get('camera_enabled') is False
            if not (camera_off and exam.enable_proctoring):
                continue

            settings = dict(Exam.PROCTOR_DEFAULTS)
            settings.update(saved)
            settings['camera_enabled'] = True
            settings['face_detection'] = True

            repaired.append((exam.id, exam.title))
            if apply_changes:
                exam.proctor_settings = json.dumps(settings)

        if apply_changes and repaired:
            db.session.commit()

        print()
        if not repaired:
            print('Nothing to repair — no exam has the buggy camera_enabled=false.')
        else:
            verb = 'Repaired' if apply_changes else 'Would repair'
            print(f'{verb} {len(repaired)} exam(s):')
            for exam_id, title in repaired:
                print(f'  #{exam_id}  {title}')
            if not apply_changes:
                print('\nRe-run with --apply to write these changes.')
        print()


if __name__ == '__main__':
    main()
