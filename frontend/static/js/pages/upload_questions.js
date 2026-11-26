/* Add questions (faculty/upload_questions.html)
 * Choosing a file checks it on the server with the same rules as the upload,
 * shows the problems row by row or a preview of what will be added, and only
 * then enables the upload button. */
(function () {
  'use strict';
  var form = document.getElementById('uploadForm');
  if (!form) return;
  var input = document.getElementById('fileInput');
  var drop = document.getElementById('fileDrop');
  var submit = document.getElementById('submitBtn');
  var result = document.getElementById('checkResult');
  var summary = document.getElementById('checkSummary');
  var errorsEl = document.getElementById('checkErrors');
  var tableWrap = document.getElementById('checkTableWrap');
  var rowsEl = document.getElementById('checkRows');

  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function tone(name) { summary.className = 'nx-alert nx-tone-' + name; }

  function check(file) {
    document.getElementById('fileMain').textContent = file.name;
    document.getElementById('fileSub').textContent = Math.max(1, Math.round(file.size / 1024)) + ' KB · click to choose another';
    drop.classList.add('has-file');
    submit.disabled = true;
    result.hidden = false;
    tone('accent');
    summary.textContent = 'Checking the file…';
    errorsEl.hidden = true; tableWrap.hidden = true;

    var fd = new FormData();
    fd.append('file', file);
    fetch(form.getAttribute('data-preview-url'), { method: 'POST', body: fd, headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        errorsEl.textContent = '';
        rowsEl.textContent = '';
        if (data.error_count) {
          tone('danger');
          summary.textContent = data.error_count + ' problem' + (data.error_count === 1 ? '' : 's') +
            ' to fix. Nothing will be added until the file is clean.';
          data.errors.forEach(function (msg) { errorsEl.appendChild(el('li', msg)); });
          if (data.error_count > data.errors.length) errorsEl.appendChild(el('li', '…and ' + (data.error_count - data.errors.length) + ' more'));
          errorsEl.hidden = false;
          drop.classList.remove('has-file');
          return;
        }
        if (!data.total) { tone('warning'); summary.textContent = 'The file has no questions.'; return; }
        tone('success');
        summary.textContent = data.total + ' question' + (data.total === 1 ? '' : 's') + ', ' + data.total_points +
          ' point' + (data.total_points === 1 ? '' : 's') + ', ready to add.' +
          (data.total > data.questions.length ? ' Showing the first ' + data.questions.length + '.' : '');
        data.questions.forEach(function (q, i) {
          var tr = document.createElement('tr');
          tr.appendChild(el('td', String(i + 1), 'nx-num'));
          tr.appendChild(el('td', q.question, 'nx-cell-clip'));
          tr.appendChild(el('td', [q.option_a, q.option_b, q.option_c, q.option_d].filter(Boolean).length + ' options'));
          tr.appendChild(el('td', q.correct_answer, 'nx-num'));
          tr.appendChild(el('td', String(q.points), 'is-num nx-num'));
          rowsEl.appendChild(tr);
        });
        tableWrap.hidden = false;
        submit.disabled = false;
      })
      .catch(function (e) {
        tone('danger');
        summary.textContent = (e && e.message) || 'Could not check the file.';
      });
  }

  input.addEventListener('change', function () { if (input.files[0]) check(input.files[0]); });
  ['dragenter', 'dragover'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('is-over'); }); });
  ['dragleave', 'drop'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('is-over'); }); });
  drop.addEventListener('drop', function (e) {
    var f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    try { input.files = e.dataTransfer.files; } catch (_) {}
    check(f);
  });
  form.addEventListener('submit', function () {
    submit.disabled = true;
    submit.querySelector('span').textContent = 'Adding…';
  });
})();
