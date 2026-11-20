/* Question splitter (faculty/split_questions.html): preview the split as
 * options change, then download the sets as a ZIP. */
(function () {
  'use strict';
  var root = document.getElementById('splitter');
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };
  var drop = $('sqDrop'), input = $('sqFile'), msg = $('sqMsg'), preview = $('sqPreview'), download = $('sqDownloadBtn');
  var file = null;

  function say(text, tone) {
    msg.hidden = !text;
    msg.className = 'nx-alert nx-tone-' + (tone || 'accent');
    msg.textContent = text || '';
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function payload() {
    var fd = new FormData();
    fd.append('file', file);
    fd.append('num_sets', $('sqSets').value || '');
    fd.append('per_set', $('sqPerSet').value || '');
    fd.append('strategy', $('sqUnique').checked ? 'unique' : 'shared');
    fd.append('balance_marks', $('sqBalance').checked ? 'true' : 'false');
    fd.append('format', $('sqFormat').value);
    return fd;
  }
  function render(plan) {
    preview.textContent = '';
    var list = el('ul', 'nx-results nx-results--pad');
    plan.sets.forEach(function (s) {
      var li = el('li', 'nx-results__item');
      var text = el('div');
      text.appendChild(el('b', null, 'Set ' + s.label));
      text.appendChild(el('span', null, s.count + ' questions · ' + s.total_marks + ' marks'));
      li.appendChild(text);
      list.appendChild(li);
    });
    preview.appendChild(list);
    var notes = [plan.bank_total + ' questions into ' + plan.num_sets + ' sets of ' + plan.per_set + '.'];
    if (plan.leftover) notes.push(plan.leftover + ' left over and not used.');
    if (plan.strategy === 'shared') notes.push('Questions may appear in more than one set.');
    $('sqPlanSub').textContent = notes.join(' ');
  }
  function check() {
    if (!file) return;
    fetch(root.getAttribute('data-preview-url'), { method: 'POST', body: payload() })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) { render(data.plan); say(''); download.disabled = false; }
        else { say((data.parsed ? data.parsed + '\n' : '') + (data.message || 'Could not read the file.'), 'danger'); download.disabled = true; }
      })
      .catch(function () { say('Could not reach the server.', 'danger'); });
  }
  function setFile(f) {
    file = f || null;
    drop.classList.toggle('has-file', !!file);
    $('sqDropMain').textContent = file ? file.name : 'Choose a question file';
    $('sqDropSub').textContent = file ? Math.max(1, Math.round(file.size / 1024)) + ' KB · click to choose another'
                                      : 'CSV, JSON or Excel · click or drop it here';
    download.disabled = true;
    check();
  }
  input.addEventListener('change', function () { setFile(input.files[0]); });
  ['dragenter', 'dragover'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('is-over'); }); });
  ['dragleave', 'drop'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('is-over'); }); });
  drop.addEventListener('drop', function (e) {
    var f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { try { input.files = e.dataTransfer.files; } catch (_) {} setFile(f); }
  });
  ['sqSets', 'sqPerSet', 'sqUnique', 'sqBalance'].forEach(function (id) { $(id).addEventListener('change', check); });

  download.addEventListener('click', function () {
    if (!file) return;
    download.disabled = true;
    say('Splitting…');
    fetch(root.getAttribute('data-download-url'), { method: 'POST', body: payload() })
      .then(function (r) {
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.message || 'Could not split the file.'); });
        return r.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = (file.name.replace(/\.[^.]+$/, '') || 'question_bank') + '_sets.zip';
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
        say('Downloaded. Each set is a separate file in the ZIP, ready for an exam\'s Add questions page.', 'success');
      })
      .catch(function (e) { say(e.message, 'danger'); })
      .then(function () { download.disabled = false; });
  });
})();
