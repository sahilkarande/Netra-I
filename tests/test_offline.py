"""P0-5: Netra-I must work on a network with no internet access.

Every asset a page loads has to come from this server. These tests fail the
build as soon as a template or script points at an external host again.
"""

import os
import re

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FRONTEND = os.path.join(ROOT, 'frontend')
VENDOR = os.path.join(FRONTEND, 'static', 'vendor')

# Links a person clicks are fine; only things the browser fetches by itself matter.
ALLOWED = (
    'https://skarande220.pythonanywhere.com',   # footer "Contact" link
    'http://www.w3.org/2000/svg',               # SVG namespace inside data: URIs
)

URL = re.compile(r'''https?://[^\s'"()<>]+''')


def _scanned_files():
    for base, _dirs, files in os.walk(FRONTEND):
        if base.startswith(VENDOR) or 'violations' in base:
            continue
        for name in files:
            if name.endswith(('.html', '.js', '.css')):
                yield os.path.join(base, name)


def test_no_external_hosts_in_templates_scripts_or_styles():
    offenders = []
    for path in _scanned_files():
        with open(path, encoding='utf-8', errors='ignore') as f:
            for lineno, line in enumerate(f, 1):
                for url in URL.findall(line):
                    if not url.startswith(ALLOWED):
                        offenders.append(f'{os.path.relpath(path, ROOT)}:{lineno}: {url}')
    assert not offenders, 'External assets break offline exams:\n' + '\n'.join(offenders)


def test_every_vendor_reference_exists():
    missing = []
    pattern = re.compile(r"filename='(vendor/[^']+)'")
    for path in _scanned_files():
        with open(path, encoding='utf-8', errors='ignore') as f:
            for ref in pattern.findall(f.read()):
                if not os.path.exists(os.path.join(FRONTEND, 'static', ref)):
                    missing.append(f'{os.path.relpath(path, ROOT)} -> {ref}')
    assert not missing, '\n'.join(missing)


@pytest.mark.parametrize('model', [
    'tiny_face_detector_model', 'face_landmark_68_tiny_model', 'face_recognition_model'])
def test_face_models_are_complete(model):
    import json
    folder = os.path.join(VENDOR, 'face-api', 'models')
    with open(os.path.join(folder, f'{model}-weights_manifest.json')) as f:
        manifest = json.load(f)
    for group in manifest:
        for shard in group['paths']:
            assert os.path.getsize(os.path.join(folder, shard)) > 0, shard


def test_assets_are_served(app, seed, as_user):
    client = as_user('stud1')
    for ref in ('vendor/socket.io/socket.io.min.js',
                'vendor/face-api/face-api.min.js',
                'vendor/face-api/models/tiny_face_detector_model-weights_manifest.json',
                'vendor/bootstrap/bootstrap.min.css',
                'vendor/bootstrap-icons/fonts/bootstrap-icons.woff2',
                'vendor/fonts/fonts.css',
                'vendor/chartjs/chart.umd.js'):
        resp = client.get(f'/static/{ref}')
        assert resp.status_code == 200, ref
        resp.close()


def test_pages_point_face_checks_at_local_models(app, seed, as_user):
    html = app.test_client().get('/login').data.decode()
    assert 'window.NETRA_FACE_MODEL_URL = "/static/vendor/face-api/models"' in html
    assert 'window.NETRA_FACEAPI_SRC = "/static/vendor/face-api/face-api.min.js"' in html
