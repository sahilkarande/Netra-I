/*
 * Faculty dashboard behaviour
 *   - exam search across the table and card views
 *   - table / cards view, remembered per browser
 *   - results and leaderboard switches (POST to the existing endpoints)
 *   - two-step delete, without window.confirm()
 *   - collapsible latest submissions
 */
(function () {
  'use strict';

  var root = document.getElementById('faculty-dashboard');
  var toast = function (msg, tone) { if (window.nx && window.nx.toast) window.nx.toast(msg, tone); };

  /* ── Search ──────────────────────────────────────────────── */
  var search = document.getElementById('examSearch');
  var noMatch = document.getElementById('examNoMatch');

  function currentView() {
    var table = document.getElementById('facultyExamTable');
    return table && !table.hidden ? table : document.getElementById('facultyExamGrid');
  }

  function applySearch() {
    if (!search) return;
    var q = search.value.trim().toLowerCase();
    document.querySelectorAll('[data-exam-row]').forEach(function (row) {
      row.hidden = q !== '' && (row.getAttribute('data-search') || '').indexOf(q) === -1;
    });
    var view = currentView();
    var visible = view ? view.querySelectorAll('[data-exam-row]:not([hidden])').length : 1;
    if (noMatch) noMatch.hidden = visible > 0;
    if (view) view.style.display = visible > 0 ? '' : 'none';
  }
  if (search) {
    search.addEventListener('input', applySearch);
    document.addEventListener('keydown', function (e) {
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === '/' && !typing) { e.preventDefault(); search.focus(); }
    });
  }

  /* ── View: table or cards ────────────────────────────────── */
  var VIEW_KEY = 'facultyExamsView';
  function setView(mode) {
    var table = document.getElementById('facultyExamTable');
    var grid = document.getElementById('facultyExamGrid');
    if (!table || !grid) return;
    table.hidden = mode !== 'table';
    grid.hidden = mode !== 'grid';
    table.style.display = '';
    grid.style.display = '';
    document.querySelectorAll('[data-exam-view]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-exam-view') === mode));
    });
    try { localStorage.setItem(VIEW_KEY, mode); } catch (e) {}
    applySearch();
  }
  document.querySelectorAll('[data-exam-view]').forEach(function (b) {
    b.addEventListener('click', function () { setView(b.getAttribute('data-exam-view')); });
  });
  var savedView = 'table';
  try { savedView = localStorage.getItem(VIEW_KEY) === 'grid' ? 'grid' : 'table'; } catch (e) {}
  setView(savedView);

  /* ── Results / leaderboard switches ──────────────────────── */
  function stateText(on, total, enabled, defaultOnly) {
    if (defaultOnly || total === 0) return 'Default for new exams';
    if (enabled) return 'On for all ' + total + ' exam' + (total === 1 ? '' : 's');
    if (on) return 'On for ' + on + ' of ' + total + ' exams';
    return 'Off';
  }

  function paint(kind, enabled, on, total, defaultOnly) {
    var input = document.querySelector('[data-visibility="' + kind + '"]');
    var label = input && input.closest('.nx-switch');
    var text = document.getElementById(kind + 'State');
    if (input) input.checked = !!enabled;
    if (label) {
      if (!enabled && on > 0) label.setAttribute('data-state', 'partial');
      else label.removeAttribute('data-state');
    }
    if (text) text.textContent = stateText(on, total, enabled, defaultOnly);
  }

  function applyState(s) {
    paint('results', s.results_enabled, s.results_on, s.total_exams, s.is_default_only);
    paint('leaderboard', s.leaderboard_enabled, s.leaderboard_on, s.total_exams, s.is_default_only);
  }

  document.querySelectorAll('[data-visibility]').forEach(function (input) {
    input.addEventListener('change', function () {
      var kind = input.getAttribute('data-visibility');
      var enabled = input.checked;
      var url = root && root.getAttribute('data-' + kind + '-url');
      var label = input.closest('.nx-switch');
      var payload = kind === 'leaderboard'
        ? { show_leaderboard: enabled, enabled: enabled }
        : { show_results: enabled, visible: enabled, enabled: enabled };

      label.setAttribute('aria-busy', 'true');
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify(payload)
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok || !res.data.success) throw new Error(res.data.message || res.data.error || 'Not saved');
          applyState(res.data);
          var d = res.data;
          if (kind === 'leaderboard' && enabled && d.total_exams > 0 && d.results_on === 0) {
            toast(d.message + '. Students will not see it until results are on.', 'warning');
          } else {
            toast(d.message || 'Saved', 'success');
          }
        })
        .catch(function (err) {
          input.checked = !enabled;
          toast((err && err.message ? err.message : 'Network error') + '. Nothing was changed.', 'danger');
        })
        .then(function () { label.removeAttribute('aria-busy'); });
    });
  });

  /* ── Delete: first press asks, second press deletes ──────── */
  document.querySelectorAll('[data-confirm-delete]').forEach(function (form) {
    var btn = form.querySelector('button[type="submit"]');
    var ask = form.querySelector('.nx-confirm__ask');
    var timer = null;

    function reset() {
      form.removeAttribute('data-armed');
      if (ask) ask.hidden = true;
      btn.classList.remove('nx-btn--danger');
      btn.classList.add('nx-btn--ghost');
      btn.setAttribute('title', 'Delete exam');
    }

    form.addEventListener('submit', function (e) {
      if (form.hasAttribute('data-armed')) return;   // second press: let it submit
      e.preventDefault();
      form.setAttribute('data-armed', '');
      if (ask) ask.hidden = false;
      btn.classList.remove('nx-btn--ghost');
      btn.classList.add('nx-btn--danger');
      btn.setAttribute('title', 'Press again to delete');
      clearTimeout(timer);
      timer = setTimeout(reset, 4000);
    });
    btn.addEventListener('blur', function () { setTimeout(function () { if (document.activeElement !== btn) reset(); }, 150); });
  });

  /* ── Latest submissions: collapse ────────────────────────── */
  var latestToggle = document.getElementById('latestToggle');
  var latestBody = document.getElementById('latestBody');
  if (latestToggle && latestBody) {
    latestToggle.addEventListener('click', function () {
      var open = latestToggle.getAttribute('aria-expanded') === 'true';
      latestBody.hidden = open;
      latestToggle.setAttribute('aria-expanded', String(!open));
      latestToggle.setAttribute('aria-label', open ? 'Show latest submissions' : 'Hide latest submissions');
      latestToggle.title = open ? 'Show' : 'Hide';
      latestToggle.querySelector('.bi').className = 'bi ' + (open ? 'bi-chevron-down' : 'bi-chevron-up');
    });
  }

  /* ── Show times in the viewer's own time zone ────────────── */
  document.querySelectorAll('.nx-feed time[datetime]').forEach(function (t) {
    var d = new Date(t.getAttribute('datetime'));
    if (!isNaN(d)) {
      t.textContent = d.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    }
  });
})();
