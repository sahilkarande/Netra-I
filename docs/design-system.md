# Netra-I Design System

The rules and building blocks for every redesigned page. Pages move onto the
system one at a time; until a page is redesigned it keeps `base.html` and the
legacy stylesheets, untouched.

**Redesigned so far:** faculty dashboard.

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
frontend/static/js/ds/app.js        theme menu, dropdowns, drawer, nx.toast()
frontend/static/js/pages/<page>.js  page behaviour (no inline scripts)

frontend/templates/
  layouts/app.html          the shell; redesigned pages extend this
  partials/                 theme_init, csrf, sidebar, topbar_end, toasts
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

**Confirmations.** Don't use `window.confirm()`. Confirm destructive actions
in place (see the two-step delete in `js/pages/faculty_dashboard.js`).

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
