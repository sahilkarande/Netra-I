# Netra-I Design System

The rules and building blocks for every redesigned page. Pages move onto the
system one at a time; until a page is redesigned it keeps `base.html` and the
legacy stylesheets, untouched.

**Redesigned so far:** faculty dashboard; sign-in flow (login, register, email verification, password change and first-login onboarding); taking an exam (access key, system check, instructions, the paper, submit); student dashboard; result page; student profile (student and staff views); exam management (exam page, analytics, new exam, add questions, question splitter).

---

## Principles

- **Quiet chrome, loud data.** Neutral surfaces, hairline borders, the numbers
  and states a person needs at the top of the page.
- **One accent, used for action.** Cobalt marks what you can press and where
  you are. Status has its own colours (green, amber, red) so "live" or
  "flagged" never competes with a button.
- **State in form, not only in numbers.** Badges, stripes and switches show
  what needs attention before anyone reads.
- **Borders before shadows.** Shadows are for things that float (menus,
  dialogs, toasts). Corners stay small: 6 px controls, 8 px panels.
- **Both themes are designed.** Dark is graphite with a lifted cobalt, not an
  inverted light theme.

---

## Files

```
frontend/static/css/ds/
  fonts.css        @font-face for the three typefaces (served from vendor/fonts)
  tokens.css       primitives → semantic tokens, light + dark
  base.css         reset, typography, focus ring, reduced motion, [hidden]
  layout.css       app shell: sidebar, top bar, page column, grids, drawer
  components.css   buttons, switches, inputs, badges, panels, stats, tables,
                   cards, feed, menus, toasts, empty states, bar chart
  exam.css         the exam runtime (system check, paper, camera widget,
                   dialogs) and the access-key gate
frontend/static/js/ds/app.js        theme menu, dropdowns, drawer, nx.toast()
frontend/static/js/pages/<page>.js  page behaviour (no inline scripts)

frontend/templates/
  layouts/app.html          the app shell (sidebar); signed-in pages extend this
  layouts/auth.html         sign-in shell (brand panel + form); set uses_face = True
                            before extending to load the face-liveness client
  layouts/exam.html         distraction-free exam shell: no sidebar, no account
                            menu; takes body_class and uses_face
  partials/                 theme_init, csrf, sidebar, topbar_end, theme_menu, toasts
  components/ui.html        Jinja macros: button, badge, stat, page_header,
                            empty_state, initials
```

`layouts/app.html` loads only the design-system CSS: no Bootstrap and no
`style.css`. Old and new styles cannot affect each other.

---

## Tokens

Components use **semantic tokens only**. Primitives (`--cobalt-600`,
`--slate-200`, `--graphite-900` …) exist to define the semantic ones and are
never referenced from components. A theme changes tokens and nothing else.

| Token | Use |
|---|---|
| `--bg-canvas` | App background |
| `--bg-surface` | Panels, tables, sidebar |
| `--bg-subtle` | Table headers, wells, segmented controls |
| `--bg-raised` | Menus, dialogs, toasts |
| `--bg-hover` / `--bg-pressed` / `--bg-selected` | Interaction states |
| `--border` / `--border-strong` | Hairlines / input outlines |
| `--text` / `--text-muted` / `--text-subtle` | Primary / secondary / captions |
| `--accent` / `--accent-hover` / `--accent-subtle` | Cobalt: actions, current nav, focus |
| `--text-on-accent` | Text on an accent fill |
| `--success` `--warning` `--danger` (+ `-subtle`) | Status only, never for actions |
| `--chart-1` / `--chart-2` / `--chart-grid` | Data series and gridlines |
| `--shadow-xs` `--shadow-sm` `--shadow-overlay` `--shadow-thumb` | Elevation |

**Theme selection.** No `data-theme` on `<html>` follows the device.
`data-theme="light"` or `"dark"` forces one. The choice is stored under
`localStorage["netra-theme"]` ("light", "dark", or absent for the device) and
applied before first paint by `partials/theme_init.html`.

**Adding a token.** Define it on the bare `:root` (light) *and* in both dark
blocks, which must stay identical. `tests/test_design_system.py` enforces
both, and checks AA contrast for every text pairing.

### Scales

| | Values |
|---|---|
| Type | `--text-2xs` 11 · `xs` 12 · `sm` 13 · `base` 15 · `md` 17 · `lg` 20 · `xl` 24 · `2xl` 30 · `3xl` 40 px |
| Space (4 px grid) | `--space-1` 4 · `2` 8 · `3` 12 · `4` 16 · `5` 20 · `6` 24 · `8` 32 · `10` 40 · `12` 48 · `16` 64 |
| Radius | `--radius-sm` 4 badges · `md` 6 controls · `lg` 8 panels · `xl` 12 dialogs · `full` pills |
| Motion | `--duration-fast` 120 ms hover/press · `base` 180 ms menus · `slow` 260 ms drawers/entrance; `--ease-out` |

### Typefaces

| Role | Face | Token |
|---|---|---|
| Display: page titles, stat values | Instrument Sans 500–700 | `--font-display` |
| Interface: everything else | IBM Plex Sans 400–600 | `--font-body` |
| Data: PRNs, keys, timers, table headings | IBM Plex Mono 400–500 | `--font-mono` |

All three are bundled in `static/vendor/fonts`. Use `.nx-num` or
`font-variant-numeric: tabular-nums` wherever digits line up.

---

## Components

Import the macros with `{% import 'components/ui.html' as ui %}`.

| Component | Use |
|---|---|
| `ui.button(label, href=None, variant='secondary', size='md', icon=None, icon_only=False)` | `primary` for the page's main action (one per view), `secondary` for others, `ghost` for toolbar and row actions, `danger` for destructive confirmation. Icon-only buttons get the label as `aria-label`. |
| `ui.badge(label, tone, live=False)` | Status. Tones: `neutral`, `accent`, `success`, `warning`, `danger`. `live=True` pulses. |
| `ui.stat(label, value, icon, unit, note, meter)` | Summary numbers. `meter` (0–100) draws a progress bar. |
| `{% call ui.page_header(title, meta) %}…actions…{% endcall %}` | Top of every page. |
| `{% call ui.empty_state(title, body, icon) %}…button…{% endcall %}` | Any list or chart with nothing to show. Say what will appear and how to add the first item. |
| `.nx-panel` + `__head` `__title` `__sub` `__actions` `__body` | Content sections. |
| `.nx-table` (+ `.nx-table--stack`, `data-label` on cells) | Tables; `--stack` turns rows into blocks on phones. |
| `.nx-switch` (`data-state="partial"`) | On/off settings that save immediately. |
| `.nx-segmented` | Switching between views of the same data. |
| `.nx-attention__item.nx-tone-*` | "Needs attention" links with a severity stripe. |
| `.nx-menu` + `data-nx-menu-toggle="<id>"` | Dropdowns; keyboard and Esc handled by `app.js`. |
| `nx.toast(message, tone)` | Feedback after an action. Errors stay until dismissed. |

### Forms (sign-in flow)

| Component | Use |
|---|---|
| `.nx-form`, `.nx-form-row`, `.nx-field`, `.nx-label` (+ `__aside`), `.nx-help` | Form structure; `.is-invalid` on a field shows its help text as the error |
| `.nx-input-wrap` + `[data-reveal="<input id>"]` | Password with a show/hide button (`js/pages/auth_forms.js`) |
| `form[data-validate]` | Inline validation from the inputs' own `required` / `minlength` / `pattern` / `type`; no browser bubbles |
| `.nx-alert.nx-tone-*` | Inline notice inside a form |
| `.nx-otp` | Six one-digit boxes with paste support (`js/pages/verify_otp.js`) |
| `.nx-steps` | Multi-step flows; `aria-current="step"` on the current one, `.is-done` before it |
| `.nx-checklist` (`li.is-ok`) | Live requirements, e.g. password rules |
| `.nx-face` (`__stage`, `__oval`, `__status`, `__dots`, `__actions`) | Camera capture for the face-liveness client |
| `.nx-divider` | Labelled separator ("Or sign in with your face") |

### Student dashboard

| Component | Use |
|---|---|
| `.nx-select` | Native `<select>` styled like an input, with a chevron |
| `.nx-trend` (`__row`, `__track`, `__fill--pass/--fail`, `__mark`) | One bar per result with a tick at the pass mark; set `--w` and `--at` |
| `.nx-dl` | Label / value pairs ("Your details") |
| `.nx-tone-neutral` | Grey stripe for informational attention items |

Exam state for a student (open, upcoming, in progress, submitted, result out,
invalidated, closed) comes from `backend/services/student_dashboard_service.py`.
Offer only the action the server will accept for that state.

### Result page and profile

| Component | Use |
|---|---|
| `.nx-result` (`__score`, `__pct`, `__bar`, `__facts`) | Score card: percentage, points, pass/fail, bar with pass-mark tick, counts |
| `.nx-review` (`__item--correct/--incorrect/--skipped`, `__opt.is-correct/.is-wrong`) | Answer review; every question on the paper, skipped ones included |
| `.nx-prose`, `.nx-code` | Question and option text. Render it with the `qtext` filter (`backend/services/text_format.py`), which escapes everything and then adds back fenced and inline code |
| `.nx-count` | Count inside a segmented button |
| `.nx-reset` | Staff password reset in two steps, showing the temporary password once |

Stored times are UTC: print them with the `localtime` filter, e.g.
`{{ se.submitted_at|localtime('%d %b %Y, %H:%M') }}`.

**Confirmations.** Don't use `window.confirm()`, `alert()` or `prompt()`.
Give the button `data-nx-confirm="End now?"`: inside a form it is wired
automatically (first press arms it, second press within 4 s submits); for a
scripted action call `nx.confirmPress(button)`, which returns true on the
second press. Report results with `nx.toast()`.

### Dialogs and exam management

| Component | Use |
|---|---|
| `<dialog class="nx-dialog">` (`--wide`, `__head`, `__title`, `__body`, `__foot`) | Modal work: open with `showModal()`. The browser traps focus, closes on Esc and makes the page inert |
| `ui.score_chart(bands)` | Score-band bar chart as SVG; bands from `dashboard_service.band_counts()` |
| `.nx-choice` + `__item` (`--row`, `--3`) | Radio or checkbox cards; the checked one is highlighted with `:has()` |
| `.nx-chips` / `.nx-chip[aria-pressed]` | Quick values for a number input |
| `.nx-drop` | File drop zone: a `<label for>` the hidden file input, so click and keyboard work natively |
| `.nx-key` | An access key, large and selectable |
| `.nx-disclosure` | A panel that opens with `<details>` |
| `.nx-picker-grid` / `.nx-pick` | Selectable people in a dialog |
| `.nx-input-unit` | Number input with a unit (`min`, `%`) inside it |

Server-read checkboxes: an unticked box sends nothing, which the server
treats as its default. Follow each with `<input type="hidden" name="…"
value="false">` (see the `toggle` macro in `faculty/create_exam.html`).

### Exam runtime

`student/take_exam.html` keeps its proctoring, timer and autosave script
inline, and that script toggles the exam's own class names (`qn-ans`,
`opt-selected`, `timer-box.crit`, `toast-item.tw` …). Those names are styled
in `exam.css` on tokens; the old `--exam-*` variables are aliases for
semantic tokens. Rules for changing it:

- Keep every element ID: the script and `tests/test_design_system.py` rely on them.
- Text from the server or the OS (toasts, the activity log, device names)
  is set with `textContent`, never `innerHTML`.
- Canvas drawing reads colours from tokens with `examColour('--success')`.
- No emoji; use Bootstrap Icons.

---

## Layout

- The sidebar holds navigation for the signed-in role; the current page is
  marked with `aria-current="page"`. Add new destinations in
  `partials/sidebar.html`.
- Pages fill `{% block content %}`; the top bar shows `{% block topbar_title %}`.
- `.nx-grid-stats` (4 → 2 columns), `.nx-grid-main` (content + 360 px side
  column, stacked below 1180 px), `.nx-stack` for vertical groups.
- Below 1024 px the sidebar becomes a drawer opened from the top bar.

---

## Motion

- Hover and press: 120 ms. Menus and toasts: 180 ms. Drawer and entrance: 260 ms.
- Page sections rise in once, staggered with `class="nx-enter" style="--i: N"`.
- `prefers-reduced-motion` turns all of it off.
- No motion near exam question text.

---

## Redesigning the next page

1. Switch the template to `{% extends "layouts/app.html" %}` and fill
   `title`, `topbar_title` and `content`.
2. Build it from the macros and `nx-` classes. Add a component to
   `components.css` (and a macro, if it takes parameters) only when an
   existing one doesn't fit.
3. Move page JavaScript into `static/js/pages/<page>.js`; pass URLs through
   `data-` attributes.
4. Keep every existing route, form field, endpoint and element ID that
   scripts or tests rely on.
5. Add the template and its script to `REDESIGNED` in
   `tests/test_design_system.py`, and add tests for the page's states.
6. Check desktop, tablet and phone widths in both themes, then run the full
   suite.
