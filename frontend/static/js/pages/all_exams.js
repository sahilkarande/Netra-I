/* All exams (admin): search and status filter. */
(function () {
  'use strict';
  var root = document.getElementById('allExams');
  if (!root) return;
  var rows = root.querySelectorAll('[data-exam-row]');
  var search = document.getElementById('examSearch');
  var status = '';
  function apply() {
    var q = search.value.trim().toLowerCase(), shown = 0;
    var wanted = status ? status.split(' ') : null;
    rows.forEach(function (r) {
      var ok = (!q || r.getAttribute('data-search').indexOf(q) !== -1) && (!wanted || wanted.indexOf(r.getAttribute('data-status')) !== -1);
      r.hidden = !ok;
      if (ok) shown++;
    });
    var none = document.getElementById('examNone');
    if (none) none.hidden = shown > 0;
  }
  search.addEventListener('input', apply);
  root.querySelectorAll('[data-status-filter]').forEach(function (b) {
    b.addEventListener('click', function () {
      status = b.getAttribute('data-status-filter');
      root.querySelectorAll('[data-status-filter]').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      apply();
    });
  });
})();
