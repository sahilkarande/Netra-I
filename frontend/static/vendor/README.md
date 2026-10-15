# Vendored front-end assets

Every third-party file the pages load is served from here, so Netra-I works on a
network with **no internet access**. Nothing in `frontend/templates` or
`frontend/static/js` may reference an external host —
`tests/test_offline.py` fails if one does.

| Folder | Package | Version | Source |
|---|---|---|---|
| `bootstrap/` | bootstrap | 5.3.3 | npm |
| `bootstrap-icons/` | bootstrap-icons | 1.11.3 | npm |
| `chartjs/` | chart.js | 4.4.0 | npm |
| `papaparse/` | papaparse | 5.3.2 | npm |
| `xlsx/` | xlsx (SheetJS CE) | 0.18.5 | npm |
| `socket.io/` | socket.io-client | 4.6.0 | npm (previously `static/js/`) |
| `face-api/` | face-api.js | 0.22.2 | npm |
| `face-api/models/` | tiny_face_detector, face_landmark_68_tiny, face_recognition weights | 0.22.2 | github.com/justadudewhohacks/face-api.js `weights/` |
| `fonts/` | @fontsource/outfit, @fontsource/plus-jakarta-sans | 5.3.0 | npm (latin, weights 300–700) |

Each folder keeps the upstream licence file. To upgrade, replace the files and
update this table; do not point templates back at a CDN.
