"""Netra-I backend package.

Imported before anything else in the backend, so process-wide settings that
must precede third-party imports live here.
"""
import sys

# ── No OpenVINO telemetry ───────────────────────────────────────────────────
# `import openvino` loads its model-conversion tools, which start Intel's
# usage telemetry (Google Analytics) unless a consent file says otherwise. It
# sends from a *forked* process: the child inherits the server's listening
# socket and, for the few seconds it lives, accepts exam connections it cannot
# serve, so students connecting right after start-up lose their proctoring
# session ("Invalid session"). On a connected machine it also reports usage to
# Google, which an exam server must not do.
#
# Marking the package unavailable makes OpenVINO fall back to its own built-in
# no-op stub. This must run before openvino is first imported.
if 'openvino_telemetry' not in sys.modules or sys.modules['openvino_telemetry'] is None:
    sys.modules['openvino_telemetry'] = None
