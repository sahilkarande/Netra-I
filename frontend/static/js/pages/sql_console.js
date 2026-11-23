/* SQL console (admin). Reads run straight away; anything else needs the
 * Allow changes switch and a second press. Results are built as DOM text. */
(function () {
  'use strict';
  var root = document.getElementById('sqlConsole');
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };
  var box = $('sqlQuery'), run = $('sqlRun'), allow = $('allowWrite'), status = $('sqlStatus'), table = $('sqlTable');
  var READ = /^\s*(select|pragma|with|explain)\b/i;

  root.querySelectorAll('[data-snippet]').forEach(function (b) {
    b.addEventListener('click', function () { box.value = b.getAttribute('data-snippet'); box.focus(); });
  });
  allow.addEventListener('change', function () { run.classList.toggle('nx-btn--danger', allow.checked); run.classList.toggle('nx-btn--primary', !allow.checked); });

  function say(text, tone) { status.textContent = text; status.className = 'nx-panel__sub' + (tone ? ' nx-text-' + tone : ''); }
  function render(cols, rows) {
    table.textContent = '';
    if (!cols.length) return;
    var head = table.createTHead().insertRow();
    cols.forEach(function (c) { var th = document.createElement('th'); th.scope = 'col'; th.textContent = c; head.appendChild(th); });
    var body = table.createTBody();
    rows.forEach(function (r) {
      var tr = body.insertRow();
      cols.forEach(function (c) {
        var td = tr.insertCell();
        var v = r[c];
        td.textContent = v === null || v === undefined ? 'NULL' : String(v);
        if (v === null || v === undefined) td.className = 'nx-table__muted';
      });
    });
  }

  function go() {
    var sql = box.value.trim();
    if (!sql) { say('Type a query first.', 'warning'); return; }
    var write = !READ.test(sql);
    if (write && !allow.checked) { say('This statement changes data. Turn on Allow changes to run it.', 'warning'); return; }
    if (write && window.nx && window.nx.confirmPress && !window.nx.confirmPress(run)) return;
    run.disabled = true;
    say('Running…');
    fetch(root.getAttribute('data-run-url'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ sql: sql, allow_write: write && allow.checked })
    })
      .then(function (r) { return r.json().catch(function () { return { error: 'The server sent no reply.' }; }); })
      .then(function (d) {
        if (!d.success) { render([], []); say(d.error || 'Failed.', 'danger'); return; }
        if (d.type === 'select') {
          render(d.columns || [], d.rows || []);
          say(d.rowcount + ' row' + (d.rowcount === 1 ? '' : 's') + (d.rowcount >= 1000 ? ' (first 1,000)' : ''));
        } else {
          render([], []);
          say(d.message || 'Done.', 'warning');
          allow.checked = false;
          allow.dispatchEvent(new Event('change'));
        }
      })
      .catch(function () { say('Could not reach the server.', 'danger'); })
      .then(function () { run.disabled = false; });
  }
  run.addEventListener('click', go);
  box.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); go(); }
  });
})();
