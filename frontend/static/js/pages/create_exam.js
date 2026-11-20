/*
 * New exam (faculty/create_exam.html)
 *   One exam: a normal form POST.
 *   Batch: rows of titles (+ optional question file each) sent to the batch
 *          endpoint with the shared settings; results shown in a dialog.
 *   Split: one question file split into sets and downloaded; nothing created.
 * Shared settings (timing, mode, proctoring, delivery) apply to one and batch.
 */
(function () {
  'use strict';

  var form = document.getElementById('cx-form');
  if (!form) return;
  var $ = function (id) { return document.getElementById(id); };
  var toast = function (msg, tone) { if (window.nx && window.nx.toast) window.nx.toast(msg, tone); };
  var URLS = {
    batch: form.getAttribute('data-batch-url'),
    preview: form.getAttribute('data-split-preview-url'),
    download: form.getAttribute('data-split-download-url'),
    exam: form.getAttribute('data-exam-url')
  };
  var mode = 'single';
  var busy = false;
  var touched = false;

  /* ── Mode ────────────────────────────────────────────────── */
  var PANELS = { single: 'panel-single', batch: 'panel-batch', split: 'panel-split' };
  var LABELS = { single: ['Create exam', 'bi-plus-lg'], batch: ['Create all exams', 'bi-check2-all'], split: ['Split and download', 'bi-download'] };

  function setMode(next) {
    if (!PANELS[next]) return;
    mode = next;
    document.querySelectorAll('[data-mode]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-mode') === next));
    });
    Object.keys(PANELS).forEach(function (k) { $(PANELS[k]).hidden = k !== next; });
    var splitting = next === 'split';
    document.querySelectorAll('.cx-shared').forEach(function (el) { el.hidden = splitting; });
    document.querySelectorAll('[data-exam-only]').forEach(function (el) { el.hidden = splitting; });
    document.querySelectorAll('[data-split-only]').forEach(function (el) { el.hidden = !splitting; });
    $('cx-submit-text').textContent = LABELS[next][0];
    $('cx-submit-icon').className = 'bi ' + LABELS[next][1];
    try { history.replaceState(null, '', '?mode=' + next); } catch (e) {}
    if (next === 'batch' && !rows().length) addRow();
    updateSummary();
  }
  document.querySelectorAll('[data-mode]').forEach(function (b) {
    b.addEventListener('click', function () { setMode(b.getAttribute('data-mode')); });
  });

  /* ── Proctoring ──────────────────────────────────────────── */
  function syncFeatures() {
    var camera = $('ps_camera').checked;
    document.querySelectorAll('.nx-feature[data-needs-camera]').forEach(function (row) {
      var input = row.querySelector('input');
      input.disabled = !camera;
      if (!camera) input.checked = false;
      row.classList.toggle('is-disabled', !camera);
    });
  }
  function collectProctor() {
    var camera = $('ps_camera').checked;
    var sens = form.querySelector('[name=ps_sensitivity]:checked');
    var settings = {
      camera_enabled: camera,
      face_detection: camera,
      head_pose: camera && $('ps_head_pose').checked,
      object_detection: camera && $('ps_objects').checked,
      voice_detection: $('ps_voice').checked,
      tab_switch: $('ps_tabswitch').checked,
      sensitivity: sens ? sens.value : 'medium',
      max_warnings: parseInt($('ps_max_warnings').value || '25', 10),
      max_tab_switches: parseInt($('max_tab_switches').value || '5', 10)
    };
    $('proctor_json').value = JSON.stringify(settings);
    return settings;
  }

  /* ── Quick-pick chips ────────────────────────────────────── */
  function wireChips(attr, target) {
    var chips = document.querySelectorAll('.nx-chip[data-' + attr + ']');
    function sync() {
      chips.forEach(function (c) { c.setAttribute('aria-pressed', String(c.getAttribute('data-' + attr) === String(target.value))); });
    }
    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        target.value = chip.getAttribute('data-' + attr);
        target.dispatchEvent(new Event('input', { bubbles: true }));
        sync();
      });
    });
    target.addEventListener('input', sync);
    sync();
  }

  /* ── Summary ─────────────────────────────────────────────── */
  function updateSummary() {
    var label = $('sum-what-label'), what = $('sum-what');
    if (mode === 'batch') {
      label.textContent = 'Exams';
      var n = rows().filter(function (r) { return r.querySelector('.nx-brow__title').value.trim(); }).length;
      what.textContent = n ? n + ' to create' : '—';
    } else if (mode === 'split') {
      label.textContent = 'File';
      what.textContent = splitFile ? splitFile.name : '—';
    } else {
      label.textContent = 'Title';
      what.textContent = $('cx-title').value.trim() || '—';
    }
    $('sum-dur').textContent = ($('cx-dur').value || 60) + ' min';
    $('sum-pass').textContent = ($('cx-pass').value || 40) + '%';
    var m = form.querySelector('[name=exam_mode]:checked');
    $('sum-mode').textContent = m && m.value === 'home' ? 'Home' : 'Classroom';
    var on = [];
    if ($('ps_camera').checked) on.push('camera');
    if ($('ps_head_pose').checked) on.push('head movement');
    if ($('ps_objects').checked) on.push('objects');
    if ($('ps_voice').checked) on.push('voice');
    if ($('ps_tabswitch').checked) on.push('tab switches');
    var text = on.join(', ');
    $('sum-proctor').textContent = text ? text.charAt(0).toUpperCase() + text.slice(1) : 'Off';
    $('sum-shuffle').textContent = $('opt-shuffle').checked ? 'Shuffled' : 'Fixed order';
    $('sum-status').textContent = $('opt-active').checked ? 'Active on create' : 'Inactive draft';
    $('sum-sets').textContent = $('split-sets').value || '—';
    $('sum-overlap').textContent = $('split-unique').checked ? 'None' : 'Allowed';
  }

  /* ── Validation ──────────────────────────────────────────── */
  function mark(fieldId, helpId, message) {
    var field = $(fieldId), help = $(helpId);
    if (field) field.classList.toggle('is-invalid', !!message);
    if (help && (message || help.getAttribute('data-error'))) {
      help.textContent = message || '';
      if (message) help.setAttribute('data-error', ''); else help.removeAttribute('data-error');
    }
    return !message;
  }
  function validate(show) {
    if (mode === 'split') return true;
    var s = show || touched;
    var ok = true;
    var dur = parseInt($('cx-dur').value || '', 10);
    ok = mark('field-dur', 'hint-dur', s && !(dur >= 1 && dur <= 600) ? 'Between 1 and 600 minutes.' : '') && ok;
    var pass = parseFloat($('cx-pass').value);
    ok = mark('field-pass', 'hint-pass', s && !(pass >= 0 && pass <= 100) ? 'Between 0 and 100.' : '') && ok;
    var start = $('cx-start').value, end = $('cx-end').value, hint = $('hint-window');
    var badWindow = start && end && new Date(end) <= new Date(start);
    $('field-window').classList.toggle('is-invalid', !!badWindow);
    hint.textContent = badWindow ? 'The window must close after it opens.'
      : (start || end) ? 'Students can only begin inside this window.'
      : 'Leave both blank to open as soon as the exam is active.';
    if (badWindow) ok = false;
    if (mode === 'single') {
      ok = mark('field-title', 'hint-title', s && !$('cx-title').value.trim() ? 'Give the exam a title.' : '') && ok;
    } else if (mode === 'batch') {
      ok = rows().some(function (r) { return r.querySelector('.nx-brow__title').value.trim(); }) && ok;
    }
    return ok;
  }

  /* ── Batch rows ──────────────────────────────────────────── */
  var seq = 0;
  function rows() { return Array.prototype.slice.call(document.querySelectorAll('#batch-list .nx-brow')); }
  function renumber() {
    rows().forEach(function (row, i) { row.querySelector('.nx-brow__num').textContent = i + 1; });
    $('batch-count').textContent = rows().length;
    $('batch-empty').hidden = rows().length > 0;
    updateSummary();
  }
  function addRow(title) {
    var id = ++seq;
    var row = document.createElement('li');
    row.className = 'nx-brow';
    row.innerHTML =
      '<span class="nx-brow__num nx-num"></span>' +
      '<input type="text" class="nx-input nx-brow__title" maxlength="200" placeholder="Exam title" aria-label="Exam title">' +
      '<label class="nx-btn nx-btn--ghost nx-btn--sm nx-brow__file"><i class="bi bi-paperclip" aria-hidden="true"></i><span>Questions</span>' +
      '<input type="file" class="nx-visually-hidden" accept=".csv,.json,.xlsx,.xls"></label>' +
      '<button type="button" class="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label="Remove this exam"><i class="bi bi-trash3" aria-hidden="true"></i></button>';
    var input = row.querySelector('.nx-brow__title');
    input.value = title || '';
    input.id = 'brow-title-' + id;
    var file = row.querySelector('input[type=file]');
    file.addEventListener('change', function () {
      var f = file.files[0];
      row.querySelector('.nx-brow__file span').textContent = f ? f.name : 'Questions';
      row.querySelector('.nx-brow__file').classList.toggle('has-file', !!f);
    });
    row.querySelector('button').addEventListener('click', function () { row.remove(); renumber(); });
    input.addEventListener('input', updateSummary);
    $('batch-list').appendChild(row);
    renumber();
    return row;
  }
  $('batch-add').addEventListener('click', function () { addRow().querySelector('.nx-brow__title').focus(); });

  var pasteBox = $('batch-paste-box');
  $('batch-paste-toggle').addEventListener('click', function () { pasteBox.hidden = false; $('batch-paste-text').focus(); });
  $('batch-paste-cancel').addEventListener('click', function () { pasteBox.hidden = true; });
  $('batch-paste-add').addEventListener('click', function () {
    var titles = $('batch-paste-text').value.split('\n').map(function (t) { return t.trim(); }).filter(Boolean);
    if (!titles.length) { toast('Paste at least one title.', 'warning'); return; }
    rows().forEach(function (r) { if (!r.querySelector('.nx-brow__title').value.trim()) r.remove(); });
    titles.forEach(function (t) { addRow(t); });
    $('batch-paste-text').value = '';
    pasteBox.hidden = true;
    toast('Added ' + titles.length + ' exam' + (titles.length === 1 ? '' : 's') + '.', 'success');
  });

  /* ── Split ───────────────────────────────────────────────── */
  var splitFile = null;
  var drop = $('split-drop'), splitInput = $('split-file'), msg = $('split-msg');
  function say(text, tone) {
    msg.hidden = !text;
    msg.className = 'nx-alert nx-tone-' + (tone || 'accent');
    msg.textContent = text || '';
  }
  function setSplitFile(f) {
    splitFile = f || null;
    drop.classList.toggle('has-file', !!splitFile);
    $('split-drop-main').textContent = splitFile ? splitFile.name : 'Choose a question file';
    $('split-drop-sub').textContent = splitFile
      ? Math.max(1, Math.round(splitFile.size / 1024)) + ' KB · click to choose another'
      : 'CSV, JSON or Excel · click or drop it here';
    updateSummary();
    previewSplit();
  }
  splitInput.addEventListener('change', function () { setSplitFile(splitInput.files[0]); });
  ['dragenter', 'dragover'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('is-over'); }); });
  ['dragleave', 'drop'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('is-over'); }); });
  drop.addEventListener('drop', function (e) {
    var f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { try { splitInput.files = e.dataTransfer.files; } catch (_) {} setSplitFile(f); }
  });
  function payload() {
    var fd = new FormData();
    fd.append('file', splitFile);
    fd.append('num_sets', $('split-sets').value || '');
    fd.append('per_set', $('split-per').value || '');
    fd.append('strategy', $('split-unique').checked ? 'unique' : 'shared');
    fd.append('balance_marks', $('split-balance').checked ? 'true' : 'false');
    fd.append('format', $('split-format').value);
    return fd;
  }
  function previewSplit() {
    if (!splitFile) return;
    fetch(URLS.preview, { method: 'POST', body: payload() })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.success) { say(data.message || 'Could not read the file.', 'danger'); return; }
        var p = data.plan;
        var lines = [p.bank_total + ' questions into ' + p.num_sets + ' sets of ' + p.per_set +
                     (p.leftover ? ' (' + p.leftover + ' left over)' : '')];
        p.sets.forEach(function (s) { lines.push('Set ' + s.label + ': ' + s.count + ' questions, ' + s.total_marks + ' marks'); });
        say(lines.join('\n'), 'accent');
      })
      .catch(function () {});
  }
  ['split-sets', 'split-per', 'split-unique', 'split-balance'].forEach(function (id) { $(id).addEventListener('change', previewSplit); });

  function runSplit() {
    if (!splitFile) { say('Choose a question file first.', 'danger'); return; }
    say('Splitting…', 'accent');
    fetch(URLS.download, { method: 'POST', body: payload() })
      .then(function (r) {
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.message || 'Could not split the file.'); });
        return r.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = splitFile.name.replace(/\.[^.]+$/, '') + '_sets.zip';
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
        say('Downloaded. Upload each set to an exam from its Add questions page.', 'success');
      })
      .catch(function (e) { say(e.message || 'Could not reach the server.', 'danger'); });
  }

  /* ── Batch submit ────────────────────────────────────────── */
  var dialog = $('batch-result');
  function resultItem(title, detail, href, failed) {
    var li = document.createElement('li');
    li.className = 'nx-results__item' + (failed ? ' is-failed' : '');
    var text = document.createElement('div');
    var b = document.createElement('b'); b.textContent = title;
    var s = document.createElement('span'); s.textContent = detail;
    text.appendChild(b); text.appendChild(s); li.appendChild(text);
    if (href) {
      var a = document.createElement('a');
      a.className = 'nx-btn nx-btn--ghost nx-btn--sm'; a.href = href; a.textContent = 'Open';
      li.appendChild(a);
    }
    return li;
  }
  function runBatch() {
    var items = [], files = [];
    rows().forEach(function (row) {
      var title = row.querySelector('.nx-brow__title').value.trim();
      if (!title) return;
      items.push({ title: title });
      files.push(row.querySelector('input[type=file]').files[0] || null);
    });
    if (!items.length) { toast('Add at least one exam with a title.', 'warning'); return; }

    var fd = new FormData();
    fd.append('exams_json', JSON.stringify(items));
    ['duration_minutes', 'passing_score', 'start_time', 'end_time', 'max_tab_switches', 'proctor_settings_json'].forEach(function (name) {
      var el = form.querySelector('[name="' + name + '"]');
      if (el) fd.append(name, el.value);
    });
    var m = form.querySelector('[name=exam_mode]:checked');
    fd.append('exam_mode', m ? m.value : 'classroom');
    var r = form.querySelector('[name=post_exam_redirect]:checked');
    fd.append('post_exam_redirect', r ? r.value : 'dashboard');
    ['require_access_key', 'randomize_questions', 'show_leaderboard', 'is_active'].forEach(function (name) {
      var el = form.querySelector('input[type=checkbox][name="' + name + '"]');
      fd.append(name, el && el.checked ? 'true' : 'false');
    });
    files.forEach(function (f, i) { if (f) fd.append('file_' + i, f); });

    var list = $('ov-list');
    list.textContent = '';
    $('ov-title').textContent = 'Creating ' + items.length + ' exam' + (items.length === 1 ? '' : 's') + '…';
    $('ov-sub').textContent = 'This only takes a moment';
    $('ov-foot').hidden = true;
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
    busy = true;

    fetch(URLS.batch, { method: 'POST', body: fd })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        $('ov-title').textContent = data.success ? 'Done' : 'Nothing was created';
        $('ov-sub').textContent = data.success ? (data.message || '') : (data.error || 'Check the rows and try again.');
        (data.created_exams || []).forEach(function (e) {
          list.appendChild(resultItem(e.title, e.questions_count + ' questions · key ' + (e.access_key || 'none'),
                                      URLS.exam.replace(/0$/, String(Number(e.id))), false));
        });
        (data.failed || []).forEach(function (f) {
          list.appendChild(resultItem(f.title, (f.errors || []).join(' '), null, true));
        });
      })
      .catch(function () {
        $('ov-title').textContent = 'Could not reach the server';
        $('ov-sub').textContent = 'Nothing was created. Try again.';
      })
      .then(function () { $('ov-foot').hidden = false; busy = false; });
  }
  $('ov-more').addEventListener('click', function () {
    dialog.close ? dialog.close() : dialog.removeAttribute('open');
    rows().forEach(function (r) { r.remove(); });
    addRow();
  });
  dialog.addEventListener('cancel', function (e) { if (busy) e.preventDefault(); });

  /* ── Submit ──────────────────────────────────────────────── */
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (busy) return;
    touched = true;
    collectProctor();
    if (mode === 'split') { runSplit(); return; }
    if (!validate(true)) {
      var bad = form.querySelector('.is-invalid input, .is-invalid textarea');
      if (bad) { bad.focus(); bad.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      else if (mode === 'batch') toast('Add at least one exam with a title.', 'warning');
      return;
    }
    if (mode === 'batch') { runBatch(); return; }
    busy = true;
    $('cx-submit').disabled = true;
    $('cx-submit-text').textContent = 'Creating…';
    form.submit();
  });

  /* ── Keyboard ────────────────────────────────────────────── */
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.submit(); return; }
    if (e.altKey && ['1', '2', '3'].indexOf(e.key) !== -1) { e.preventDefault(); setMode({ 1: 'single', 2: 'batch', 3: 'split' }[e.key]); return; }
    var a = document.activeElement;
    if (e.key === 'Enter' && a && a.classList && a.classList.contains('nx-brow__title')) {
      e.preventDefault();
      addRow().querySelector('.nx-brow__title').focus();
    }
  });

  /* ── Wire up ─────────────────────────────────────────────── */
  document.querySelectorAll('.nx-feature input').forEach(function (i) {
    i.addEventListener('change', function () { syncFeatures(); updateSummary(); });
  });
  form.addEventListener('input', function () { validate(false); updateSummary(); });
  form.addEventListener('change', function () { validate(false); updateSummary(); });
  ['cx-title', 'cx-dur', 'cx-pass'].forEach(function (id) {
    $(id).addEventListener('blur', function () { touched = true; validate(false); });
  });
  wireChips('dur', $('cx-dur'));
  wireChips('pass', $('cx-pass'));

  var start = new URLSearchParams(location.search).get('mode');
  setMode(PANELS[start] ? start : 'single');
  syncFeatures();
  updateSummary();
})();
