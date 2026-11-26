/*
 * Students (faculty/manage_students.html)
 *   - filters submit on change
 *   - selection with a bulk bar: export or delete the selected students
 *   - delete one student (two-step)
 *   - import dialog with a drop zone and client-side size/type checks
 */
(function () {
  'use strict';
  var root = document.getElementById('students');
  var toast = function (msg, tone) { if (window.nx && window.nx.toast) window.nx.toast(msg, tone); };
  var confirmPress = function (btn) { return window.nx && window.nx.confirmPress ? window.nx.confirmPress(btn) : true; };

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || !d.success) throw new Error(d.error || 'Nothing was changed.');
        return d;
      });
    });
  }

  /* ── Filters ─────────────────────────────────────────────── */
  document.querySelectorAll('[data-autosubmit]').forEach(function (sel) {
    sel.addEventListener('change', function () { sel.form.submit(); });
  });

  if (root) {
    /* ── Selection ─────────────────────────────────────────── */
    var boxes = Array.prototype.slice.call(root.querySelectorAll('[data-student]'));
    var all = document.getElementById('selectAll');
    var bar = document.getElementById('bulkbar');
    function selected() { return boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.getAttribute('data-student'); }); }
    function sync() {
      var n = selected().length;
      if (bar) { bar.hidden = n === 0; document.getElementById('selCount').textContent = n; }
      if (all) { all.checked = n > 0 && n === boxes.length; all.indeterminate = n > 0 && n < boxes.length; }
      boxes.forEach(function (b) { b.closest('tr').classList.toggle('is-selected', b.checked); });
    }
    boxes.forEach(function (b) { b.addEventListener('change', sync); });
    if (all) all.addEventListener('change', function () { boxes.forEach(function (b) { b.checked = all.checked; }); sync(); });
    var clear = document.getElementById('clearSelection');
    if (clear) clear.addEventListener('click', function () { boxes.forEach(function (b) { b.checked = false; }); sync(); });

    var exportSel = document.getElementById('exportSelected');
    if (exportSel) exportSel.addEventListener('click', function () {
      var params = new URLSearchParams();
      selected().forEach(function (id) { params.append('ids', id); });
      window.location.href = root.getAttribute('data-export-url') + '?' + params.toString();
    });

    var delSel = document.getElementById('deleteSelected');
    if (delSel) delSel.addEventListener('click', function () {
      var ids = selected().map(Number);
      if (!ids.length || !confirmPress(delSel)) return;
      delSel.disabled = true;
      post(root.getAttribute('data-delete-many-url'), { ids: ids })
        .then(function (d) {
          toast(d.deleted + ' deleted' + (d.skipped ? ', ' + d.skipped + ' not yours to delete' : '') + '.', 'success');
          setTimeout(function () { location.reload(); }, 600);
        })
        .catch(function (e) { delSel.disabled = false; toast(e.message, 'danger'); });
    });

    /* ── Delete one ────────────────────────────────────────── */
    root.querySelectorAll('[data-delete]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!confirmPress(btn)) return;
        btn.disabled = true;
        var url = root.getAttribute('data-delete-url').replace(/0$/, btn.getAttribute('data-delete'));
        post(url)
          .then(function () {
            var row = btn.closest('tr');
            boxes = boxes.filter(function (b) { return !row.contains(b); });
            row.remove();
            sync();
            toast('Student deleted.', 'success');
          })
          .catch(function (e) { btn.disabled = false; toast(e.message, 'danger'); });
      });
    });
  }

  /* ── Import dialog ───────────────────────────────────────── */
  var dialog = document.getElementById('importDialog');
  if (!dialog) return;
  var file = document.getElementById('importFile');
  var drop = document.getElementById('importDrop');
  var submit = document.getElementById('importSubmit');
  var err = document.getElementById('importError');
  var MAX = 5 * 1024 * 1024;

  function open() { if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', ''); }
  function close() { if (dialog.close) dialog.close(); else dialog.removeAttribute('open'); }
  document.querySelectorAll('#openImport, [data-open-import]').forEach(function (b) { b.addEventListener('click', open); });
  dialog.querySelectorAll('[data-close-import]').forEach(function (b) { b.addEventListener('click', close); });
  dialog.addEventListener('click', function (e) { if (e.target === dialog) close(); });

  function choose(f) {
    err.textContent = '';
    submit.disabled = true;
    drop.classList.remove('has-file');
    if (!f) return;
    if (!/\.(csv|xlsx|xls|json)$/i.test(f.name)) { err.textContent = 'Use a CSV, Excel or JSON file.'; return; }
    if (f.size > MAX) { err.textContent = 'The file is over 5 MB.'; return; }
    document.getElementById('importMain').textContent = f.name;
    document.getElementById('importSub').textContent = Math.max(1, Math.round(f.size / 1024)) + ' KB · click to choose another';
    drop.classList.add('has-file');
    submit.disabled = false;
  }
  file.addEventListener('change', function () { choose(file.files[0]); });
  ['dragenter', 'dragover'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('is-over'); }); });
  ['dragleave', 'drop'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('is-over'); }); });
  drop.addEventListener('drop', function (e) {
    var f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    try { file.files = e.dataTransfer.files; } catch (_) {}
    choose(f);
  });
  document.getElementById('importForm').addEventListener('submit', function () {
    submit.disabled = true;
    submit.querySelector('span').textContent = 'Importing…';
  });
})();
