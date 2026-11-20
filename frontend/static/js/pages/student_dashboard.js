/*
 * Student dashboard behaviour
 *   - exam search across the table and card views ("/" focuses it)
 *   - sort: what's next (server order), newest, shortest, fewest questions
 *   - table / cards view, remembered per browser
 */
(function () {
  'use strict';

  var search = document.getElementById('examSearch');
  var sort = document.getElementById('examSort');
  var noMatch = document.getElementById('examNoMatch');
  var table = document.getElementById('studentExamTable');
  var grid = document.getElementById('studentExamGrid');
  if (!table || !grid) return;

  function currentView() { return table.hidden ? grid : table; }

  /* ── Search ──────────────────────────────────────────────── */
  function applySearch() {
    var q = search ? search.value.trim().toLowerCase() : '';
    document.querySelectorAll('[data-exam-row]').forEach(function (row) {
      row.hidden = q !== '' && (row.getAttribute('data-search') || '').indexOf(q) === -1;
    });
    var view = currentView();
    var visible = view.querySelectorAll('[data-exam-row]:not([hidden])').length;
    if (noMatch) noMatch.hidden = visible > 0;
    view.style.display = visible > 0 ? '' : 'none';
  }
  if (search) {
    search.addEventListener('input', applySearch);
    document.addEventListener('keydown', function (e) {
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === '/' && !typing) { e.preventDefault(); search.focus(); }
    });
  }

  /* ── Sort ────────────────────────────────────────────────── */
  function num(el, attr) { return parseFloat(el.getAttribute(attr)) || 0; }
  var compare = {
    next: function (a, b) { return num(a, 'data-order') - num(b, 'data-order'); },
    newest: function (a, b) { return (b.getAttribute('data-created') || '').localeCompare(a.getAttribute('data-created') || ''); },
    duration: function (a, b) { return num(a, 'data-duration') - num(b, 'data-duration'); },
    questions: function (a, b) { return num(a, 'data-questions') - num(b, 'data-questions'); }
  };
  function applySort() {
    var by = compare[sort.value] || compare.next;
    document.querySelectorAll('[data-exam-list]').forEach(function (list) {
      Array.prototype.slice.call(list.querySelectorAll('[data-exam-row]'))
        .sort(by)
        .forEach(function (row) { list.appendChild(row); });
    });
  }
  if (sort) sort.addEventListener('change', applySort);

  /* ── View: table or cards ────────────────────────────────── */
  var VIEW_KEY = 'studentExamsView';
  function setView(mode) {
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
  var saved = 'table';
  try { saved = localStorage.getItem(VIEW_KEY) === 'grid' ? 'grid' : 'table'; } catch (e) {}
  setView(saved);
})();
