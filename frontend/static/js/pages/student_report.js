/*
 * Batch report (faculty/student_report.html)
 *   Build form: filter and pick exams.
 *   Report: filters, sorting, edit mode (marks, column names and notes) with
 *   live totals, per-row undo, a change list, find and replace, CSV of what
 *   is shown, and a PDF built on the server from the recorded marks plus the
 *   edits and layout chosen here. Edits never touch the database.
 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var toast = function (msg, tone) { if (window.nx && window.nx.toast) window.nx.toast(msg, tone); };

  /* ── Build form ──────────────────────────────────────────── */
  var build = $('buildForm');
  if (build) {
    var items = Array.prototype.slice.call(document.querySelectorAll('.nx-examlist__item'));
    var filter = $('examFilter');
    if (filter) filter.addEventListener('input', function () {
      var q = filter.value.trim().toLowerCase();
      items.forEach(function (it) { it.hidden = q && it.getAttribute('data-title').indexOf(q) === -1; });
    });
    function setAll(on) { items.forEach(function (it) { if (!it.hidden) it.querySelector('input').checked = on; }); }
    if ($('examsAll')) $('examsAll').addEventListener('click', function () { setAll(true); });
    if ($('examsNone')) $('examsNone').addEventListener('click', function () { setAll(false); });
    build.addEventListener('submit', function (e) {
      var any = build.querySelector('input[name=exam_ids]:checked');
      $('examError').hidden = !!any;
      if (!any) e.preventDefault();
    });
  }

  var report = $('report');
  if (!report) return;
  var PASS = parseFloat(report.getAttribute('data-pass')) || 50;
  var tbody = report.querySelector('tbody');
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr[data-row]'));
  var editing = false;
  var changes = [];   // {key, label, from, to}

  function num(v) { var n = parseFloat(v); return isNaN(n) ? null : n; }
  function fmt(n) { return String(Math.round(n * 100) / 100); }

  /* ── Row maths ───────────────────────────────────────────── */
  function badge(status) {
    var map = { pass: ['Pass', 'success'], fail: ['Below', 'danger'], absent: ['Absent', 'neutral'] };
    var b = document.createElement('span');
    b.className = 'nx-badge nx-badge--' + map[status][1];
    b.textContent = map[status][0];
    return b;
  }
  function recalc(row) {
    var max = row.getAttribute('data-max').split(',').map(Number);
    var total = 0, outOf = 0, sat = false;
    row.querySelectorAll('.nx-mark').forEach(function (inp, i) {
      var n = num(inp.value);
      if (n === null) return;
      sat = true; total += n; outOf += max[i] || 0;
    });
    var pct = sat && outOf ? total / outOf * 100 : 0;
    var status = !sat ? 'absent' : (pct >= PASS ? 'pass' : 'fail');
    row.setAttribute('data-total', sat ? total : 0);
    row.setAttribute('data-pct', pct.toFixed(2));
    row.setAttribute('data-status', status);
    row.querySelector('[data-cell=total]').textContent = sat ? fmt(total) + ' / ' + fmt(outOf) : '—';
    row.querySelector('[data-cell=pct]').textContent = sat ? pct.toFixed(1) : '—';
    var cell = row.querySelector('[data-cell=status]');
    cell.textContent = '';
    cell.appendChild(badge(status));
  }
  function metrics() {
    var passed = 0, failed = 0, sum = 0, appeared = 0;
    rows.forEach(function (r) {
      var s = r.getAttribute('data-status');
      if (s === 'absent') return;
      appeared++; sum += parseFloat(r.getAttribute('data-pct')) || 0;
      if (s === 'pass') passed++; else failed++;
    });
    var rate = appeared ? passed / appeared * 100 : 0;
    $('live-passed').textContent = passed;
    $('live-appeared').textContent = appeared;
    $('live-failed').textContent = failed;
    $('live-passpct').textContent = Math.round(rate);
    $('live-avg').textContent = appeared ? Math.round(sum / appeared) : 0;
    $('live-passbar').style.setProperty('--value', rate + '%');
  }

  /* ── Change list ─────────────────────────────────────────── */
  function renderChanges() {
    var box = $('changes'), list = $('changeList');
    box.hidden = changes.length === 0;
    $('changeCount').textContent = changes.length;
    $('changePlural').textContent = changes.length === 1 ? '' : 's';
    list.textContent = '';
    changes.slice(0, 30).forEach(function (c) {
      var li = document.createElement('li');
      var a = document.createElement('span'); a.textContent = c.label;
      var b = document.createElement('span'); b.className = 'nx-num'; b.textContent = c.from + ' → ' + c.to;
      li.appendChild(a); li.appendChild(b); list.appendChild(li);
    });
  }
  function record(key, label, from, to) {
    changes = changes.filter(function (c) { return c.key !== key; });
    if (String(from) !== String(to)) changes.unshift({ key: key, label: label, from: from, to: to });
    renderChanges();
  }

  /* ── Edit mode ───────────────────────────────────────────── */
  var toggle = $('editToggle');
  toggle.addEventListener('change', function () {
    editing = toggle.checked;
    report.classList.toggle('is-editing', editing);
    $('editBar').hidden = !editing;
    report.querySelectorAll('.nx-cell-input').forEach(function (i) { i.readOnly = !editing; });
  });

  function colName(mi) { var c = report.querySelector('.nx-colname[data-col="' + mi + '"]'); return c ? c.value : 'Exam ' + (mi + 1); }

  function commitMark(inp) {
    var raw = inp.value.trim();
    var val;
    if (raw === '' || raw === '-' || /^absent$/i.test(raw)) val = 'Absent';
    else if (num(raw) === null || num(raw) < 0) { inp.value = inp.getAttribute('data-last') || inp.getAttribute('data-orig'); toast('A mark is a number, or Absent.', 'warning'); return; }
    else val = fmt(num(raw));
    var max = Number(inp.closest('tr').getAttribute('data-max').split(',')[+inp.getAttribute('data-mi')]);
    if (val !== 'Absent' && max && num(val) > max) toast('That is more than the paper\'s ' + fmt(max) + ' marks.', 'warning');
    inp.value = val;
    inp.setAttribute('data-last', val);
    var orig = inp.getAttribute('data-orig');
    inp.classList.toggle('is-changed', val !== orig);
    inp.classList.toggle('is-absent', val === 'Absent');
    var row = inp.closest('tr');
    var edited = row.querySelector('.nx-mark.is-changed');
    row.classList.toggle('is-edited', !!edited);
    row.querySelector('[data-undo]').hidden = !edited;
    recalc(row);
    metrics();
    record('m' + inp.getAttribute('data-si') + '_' + inp.getAttribute('data-mi'),
           row.querySelector('.nx-report__name').textContent + ' · ' + colName(+inp.getAttribute('data-mi')), orig, val);
  }
  report.querySelectorAll('.nx-mark').forEach(function (inp) {
    inp.addEventListener('change', function () { if (editing) commitMark(inp); });
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } });
  });
  report.querySelectorAll('.nx-colname').forEach(function (inp) {
    inp.addEventListener('change', function () {
      if (!inp.value.trim()) inp.value = inp.getAttribute('data-orig');
      record('c' + inp.getAttribute('data-col'), 'Column renamed', inp.getAttribute('data-orig'), inp.value);
    });
  });

  function undoRow(row) {
    row.querySelectorAll('.nx-mark').forEach(function (inp) {
      inp.value = inp.getAttribute('data-orig');
      inp.removeAttribute('data-last');
      inp.classList.remove('is-changed');
      inp.classList.toggle('is-absent', inp.value === 'Absent');
      changes = changes.filter(function (c) { return c.key !== 'm' + inp.getAttribute('data-si') + '_' + inp.getAttribute('data-mi'); });
    });
    row.classList.remove('is-edited');
    row.querySelector('[data-undo]').hidden = true;
    recalc(row);
  }
  rows.forEach(function (row) {
    row.querySelector('[data-undo]').addEventListener('click', function () { undoRow(row); metrics(); renderChanges(); });
  });
  var revert = $('revertAll');
  revert.addEventListener('click', function () {
    if (window.nx && window.nx.confirmPress && !window.nx.confirmPress(revert)) return;
    rows.forEach(undoRow);
    report.querySelectorAll('.nx-colname').forEach(function (i) { i.value = i.getAttribute('data-orig'); });
    report.querySelectorAll('.nx-colnote').forEach(function (i) { i.value = ''; });
    changes = [];
    metrics(); renderChanges();
  });

  /* ── Filters ─────────────────────────────────────────────── */
  var fText = $('f-text'), fStatus = $('f-status'), fMin = $('f-min'), fMax = $('f-max'), fClear = $('f-clear');
  function applyFilters() {
    var q = fText.value.trim().toLowerCase(), st = fStatus.value;
    var lo = num(fMin.value), hi = num(fMax.value);
    var shown = 0;
    rows.forEach(function (r) {
      var pct = parseFloat(r.getAttribute('data-pct')) || 0;
      var ok = (!q || r.getAttribute('data-name').indexOf(q) !== -1 || r.getAttribute('data-prn').indexOf(q) !== -1) &&
               (!st || r.getAttribute('data-status') === st) &&
               (lo === null || pct >= lo) && (hi === null || pct <= hi);
      r.hidden = !ok;
      if (ok) shown++;
    });
    $('visible-rows').textContent = shown;
    $('noRows').hidden = shown > 0;
    fClear.hidden = !(q || st || lo !== null || hi !== null);
  }
  [fText, fMin, fMax].forEach(function (el) { el.addEventListener('input', applyFilters); });
  fStatus.addEventListener('change', applyFilters);
  fClear.addEventListener('click', function () { fText.value = fMin.value = fMax.value = ''; fStatus.value = ''; applyFilters(); });

  /* ── Sorting ─────────────────────────────────────────────── */
  var sortKey = null, sortDir = 1;
  function keyOf(row, key, col) {
    if (key === 'prn') return row.getAttribute('data-prn');
    if (key === 'name') return row.getAttribute('data-name');
    if (key === 'total') return parseFloat(row.getAttribute('data-total')) || 0;
    if (key === 'pct') return parseFloat(row.getAttribute('data-pct')) || 0;
    if (key === 'status') return { pass: 0, fail: 1, absent: 2 }[row.getAttribute('data-status')];
    var v = num(row.querySelectorAll('.nx-mark')[col].value);
    return v === null ? -1 : v;
  }
  report.querySelectorAll('[data-sort]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var key = btn.getAttribute('data-sort'), col = +btn.getAttribute('data-col');
      var id = key + (key === 'exam' ? col : '');
      sortDir = sortKey === id ? -sortDir : 1;
      sortKey = id;
      rows.sort(function (a, b) {
        var x = keyOf(a, key, col), y = keyOf(b, key, col);
        return (typeof x === 'string' ? x.localeCompare(y) : x - y) * sortDir;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
      report.querySelectorAll('[data-sort]').forEach(function (b) { b.closest('th').removeAttribute('aria-sort'); });
      btn.closest('th').setAttribute('aria-sort', sortDir === 1 ? 'ascending' : 'descending');
    });
  });

  /* ── Find and replace (marks, visible rows, edit mode) ───── */
  var matches = [], cursor = -1;
  function collect() {
    report.querySelectorAll('.nx-mark.is-match').forEach(function (i) { i.classList.remove('is-match'); });
    var needle = $('fnr-find').value.trim().toLowerCase();
    matches = needle ? Array.prototype.filter.call(report.querySelectorAll('tr[data-row]:not([hidden]) .nx-mark'), function (i) {
      return i.value.toLowerCase() === needle || i.value.toLowerCase().indexOf(needle) !== -1;
    }) : [];
    matches.forEach(function (i) { i.classList.add('is-match'); });
    $('fnr-status').textContent = needle ? (matches.length ? matches.length + ' found' : 'None found') : '';
  }
  $('fnr-find').addEventListener('input', function () { cursor = -1; collect(); });
  $('fnr-next').addEventListener('click', function () {
    collect();
    if (!matches.length) return;
    cursor = (cursor + 1) % matches.length;
    matches[cursor].focus();
    matches[cursor].scrollIntoView({ block: 'center', behavior: 'smooth' });
    $('fnr-status').textContent = (cursor + 1) + ' of ' + matches.length;
  });
  function replace(inp) { inp.value = $('fnr-replace').value; commitMark(inp); inp.classList.remove('is-match'); }
  $('fnr-one').addEventListener('click', function () {
    collect();
    if (!matches.length) return;
    replace(matches[Math.max(0, cursor) % matches.length]);
    collect();
  });
  $('fnr-all').addEventListener('click', function () {
    collect();
    var n = matches.length;
    matches.forEach(replace);
    matches = [];
    $('fnr-status').textContent = n ? 'Replaced ' + n : 'None found';
  });

  /* ── CSV of what is shown ────────────────────────────────── */
  function csvCell(v) { v = String(v).replace(/[\r\n]+/g, ' ').trim(); return /[",]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
  $('exportCsv').addEventListener('click', function () {
    var names = Array.prototype.map.call(report.querySelectorAll('.nx-colname'), function (i) { return i.value; });
    var lines = [['PRN', 'Name'].concat(names, ['Total', 'Out of', 'Percentage', 'Result']).map(csvCell).join(',')];
    rows.forEach(function (r) {
      if (r.hidden) return;
      var cells = r.querySelectorAll('td');
      var marks = Array.prototype.map.call(r.querySelectorAll('.nx-mark'), function (i) { return i.value; });
      var total = r.querySelector('[data-cell=total]').textContent.split(' / ');
      lines.push([cells[0].textContent, cells[1].textContent].concat(marks, [total[0], total[1] || '', r.querySelector('[data-cell=pct]').textContent, r.getAttribute('data-status')]).map(csvCell).join(','));
    });
    var blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (report.getAttribute('data-batch') || 'batch').replace(/[^\w-]+/g, '_') + '_report.csv';
    document.body.appendChild(a); a.click(); a.remove();
  });

  /* ── PDF ─────────────────────────────────────────────────── */
  var dialog = $('pdfDialog');
  $('openPdf').addEventListener('click', function () { dialog.showModal ? dialog.showModal() : dialog.setAttribute('open', ''); });
  dialog.querySelectorAll('[data-close-pdf]').forEach(function (b) { b.addEventListener('click', function () { dialog.close(); }); });
  var make = $('makePdf');
  make.addEventListener('click', function () {
    var payload = {
      batch_name: report.getAttribute('data-batch'),
      exam_ids: report.getAttribute('data-exam-ids').split(',').map(Number).filter(function (n) { return !isNaN(n); }),
      mark_edits: {},
      col_names: [],
      col_notes: []
    };
    if ($('pdf-order').checked) payload.row_order = rows.filter(function (r) { return !r.hidden; }).map(function (r) { return +r.getAttribute('data-si'); });
    if ($('pdf-edits').checked) report.querySelectorAll('.nx-mark.is-changed').forEach(function (i) {
      payload.mark_edits[i.getAttribute('data-si') + '_' + i.getAttribute('data-mi')] = i.value;
    });
    if ($('pdf-names').checked) payload.col_names = Array.prototype.map.call(report.querySelectorAll('.nx-colname'), function (i) { return i.value; });
    if ($('pdf-notes').checked) payload.col_notes = Array.prototype.map.call(report.querySelectorAll('.nx-colnote'), function (i) { return i.value; });

    make.disabled = true;
    make.querySelector('span').textContent = 'Making PDF…';
    fetch(report.getAttribute('data-pdf-url'), {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok || !d.success) throw new Error(d.error || 'Could not make the PDF.'); return d; }); })
      .then(function (d) {
        var bytes = Uint8Array.from(atob(d.pdf_b64), function (c) { return c.charCodeAt(0); });
        var url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
        var a = document.createElement('a');
        a.href = url; a.download = d.filename || 'report.pdf';
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
        dialog.close();
      })
      .catch(function (e) { toast(e.message, 'danger'); })
      .then(function () { make.disabled = false; make.querySelector('span').textContent = 'Download'; });
  });
})();
