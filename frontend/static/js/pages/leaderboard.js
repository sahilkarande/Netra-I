/* Leaderboard: search, minimum %, sorting, compare selected students, CSV. */
(function () {
  'use strict';
  var root = document.getElementById('leaderboard');
  if (!root) return;
  var tbody = root.querySelector('tbody');
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('[data-lb-row]'));
  var $ = function (id) { return document.getElementById(id); };
  var n = function (r, k) { return parseFloat(r.getAttribute('data-' + k)) || 0; };

  function apply() {
    var q = $('lbSearch').value.trim().toLowerCase();
    var min = parseFloat($('lbMinPct').value);
    var shown = 0;
    rows.forEach(function (r) {
      var ok = (!q || r.getAttribute('data-name').indexOf(q) !== -1 || r.getAttribute('data-prn').indexOf(q) !== -1) &&
               (isNaN(min) || n(r, 'pct') >= min);
      r.hidden = !ok;
      if (ok) shown++;
    });
    $('lbVisible').textContent = shown;
    $('lbNone').hidden = shown > 0;
  }
  $('lbSearch').addEventListener('input', apply);
  $('lbMinPct').addEventListener('input', apply);

  var sortKey = null, dir = 1;
  root.querySelectorAll('[data-sort]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var k = btn.getAttribute('data-sort');
      dir = sortKey === k ? -dir : (k === 'name' || k === 'rank' || k === 'behind' ? 1 : -1);
      sortKey = k;
      rows.sort(function (a, b) {
        if (k === 'name') return a.getAttribute('data-name').localeCompare(b.getAttribute('data-name')) * dir;
        return (n(a, k) - n(b, k)) * dir;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
      root.querySelectorAll('th').forEach(function (th) { th.removeAttribute('aria-sort'); });
      btn.closest('th').setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');
    });
  });

  /* Compare */
  var bar = $('compareBar');
  function picked() { return rows.filter(function (r) { return r.querySelector('[data-compare]').checked; }); }
  function sync() { var c = picked().length; $('compareCount').textContent = c; bar.hidden = c < 2; }
  rows.forEach(function (r) { r.querySelector('[data-compare]').addEventListener('change', sync); });
  $('clearCompare').addEventListener('click', function () { rows.forEach(function (r) { r.querySelector('[data-compare]').checked = false; }); sync(); });

  var dialog = $('compareDialog');
  dialog.querySelectorAll('[data-close-compare]').forEach(function (b) { b.addEventListener('click', function () { dialog.close(); }); });
  $('openCompare').addEventListener('click', function () {
    var sel = picked();
    var cols = [['Rank', 'rank', false], ['Exams', 'exams', true], ['Answered', 'answered', true], ['Marks', 'marks', true], ['%', 'pct', true]];
    var table = $('compareTable');
    table.textContent = '';
    var head = table.createTHead().insertRow();
    ['Student'].concat(cols.map(function (c) { return c[0]; })).forEach(function (t, i) {
      var th = document.createElement('th'); th.scope = 'col'; th.textContent = t;
      if (i) th.className = 'is-num';
      head.appendChild(th);
    });
    var best = {};
    cols.forEach(function (c) {
      var vals = sel.map(function (r) { return n(r, c[1]); });
      best[c[1]] = c[2] ? Math.max.apply(null, vals) : Math.min.apply(null, vals);
    });
    var body = table.createTBody();
    sel.forEach(function (r) {
      var tr = body.insertRow();
      var name = tr.insertCell(); name.textContent = r.getAttribute('data-label-name');
      cols.forEach(function (c) {
        var td = tr.insertCell();
        var v = n(r, c[1]);
        td.className = 'is-num nx-num' + (v === best[c[1]] ? ' is-best' : '');
        td.textContent = String(Math.round(v * 10) / 10);
      });
    });
    dialog.showModal ? dialog.showModal() : dialog.setAttribute('open', '');
  });

  /* CSV of the rows shown */
  var csvBtn = $('exportCsv');
  if (csvBtn) csvBtn.addEventListener('click', function () {
    var cell = function (v) { v = String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    var lines = ['Rank,Name,PRN,Exams,Answered,Marks,Out of,Percentage,Behind'];
    rows.forEach(function (r) {
      if (r.hidden) return;
      lines.push([r.getAttribute('data-rank'), r.getAttribute('data-label-name'), r.getAttribute('data-prn'),
        r.getAttribute('data-exams'), r.getAttribute('data-answered'), r.getAttribute('data-marks'),
        r.getAttribute('data-possible'), r.getAttribute('data-pct'), r.getAttribute('data-behind')].map(cell).join(','));
    });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' }));
    a.download = 'leaderboard.csv';
    document.body.appendChild(a); a.click(); a.remove();
  });
})();
