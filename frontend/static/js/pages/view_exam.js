/*
 * Exam page (faculty/view_exam.html)
 *   - live refresh of the attempts table and summary (paused while you work)
 *   - attempt search
 *   - access key: copy, new, remove
 *   - results / leaderboard switches; publish and clear evidence
 *   - who can sit it: everyone or selected, with the student picker dialog
 *     (search, batch, roll ranges, quick picks) and the set generator
 */
(function () {
  'use strict';

  var root = document.getElementById('view-exam');
  if (!root) return;
  var examId = parseInt(root.getAttribute('data-exam-id'), 10);
  var toast = function (msg, tone) { if (window.nx && window.nx.toast) window.nx.toast(msg, tone); };
  var confirmPress = function (btn) { return window.nx && window.nx.confirmPress ? window.nx.confirmPress(btn) : true; };

  function post(url, body) {
    var opts = { method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' } };
    if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok || data.success === false) throw new Error(data.message || data.error || 'Could not save');
        return data;
      });
    });
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy') ? resolve() : reject(); } catch (e) { reject(e); }
      ta.remove();
    });
  }

  /* ── Attempt search ──────────────────────────────────────── */
  var search = document.getElementById('attemptSearch');
  function applySearch() {
    if (!search) return;
    var q = search.value.trim().toLowerCase();
    var shown = 0;
    document.querySelectorAll('[data-attempt-row]').forEach(function (row) {
      row.hidden = q !== '' && (row.getAttribute('data-search') || '').indexOf(q) === -1;
      if (!row.hidden) shown++;
    });
    var none = document.getElementById('attemptNoMatch');
    if (none) none.hidden = shown > 0;
  }
  if (search) search.addEventListener('input', applySearch);

  /* ── Live refresh ────────────────────────────────────────── */
  var picker = document.getElementById('studentPicker');
  function busy() {
    var a = document.activeElement;
    return document.hidden || (picker && picker.open) ||
      (a && a.closest && a.closest('#attemptsBody form, .nx-panel__body form, .nx-dialog')) ||
      document.querySelector('[data-armed]');
  }
  setInterval(function () {
    if (busy()) return;
    fetch(window.location.href, { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html || busy()) return;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        ['attemptsBody', 'examStats', 'attemptsSub'].forEach(function (id) {
          var fresh = doc.getElementById(id), old = document.getElementById(id);
          if (fresh && old && fresh.innerHTML !== old.innerHTML) {
            old.innerHTML = fresh.innerHTML;
            if (id === 'attemptsBody') wireForms(old);
          }
        });
        document.querySelectorAll('#examStats .nx-enter').forEach(function (el) { el.classList.remove('nx-enter'); });
        applySearch();
      })
      .catch(function () {});
  }, 5000);

  /* Forms inside refreshed rows need the two-step confirm wiring again */
  function wireForms(scope) {
    scope.querySelectorAll('form').forEach(function (form) {
      var btn = form.querySelector('button[type="submit"][data-nx-confirm]');
      if (!btn) return;
      form.addEventListener('submit', function (e) { if (!confirmPress(btn)) e.preventDefault(); });
    });
  }

  /* ── Access key ──────────────────────────────────────────── */
  document.querySelectorAll('[data-copy-key]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      copyText(btn.getAttribute('data-copy-key')).then(function () { toast('Key copied', 'success'); },
        function () { toast('Copy failed. Select the key and copy it.', 'warning'); });
    });
  });
  document.querySelectorAll('[data-new-key]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (btn.hasAttribute('data-nx-confirm') && !confirmPress(btn)) return;
      btn.disabled = true;
      post(root.getAttribute('data-key-url')).then(function () { location.reload(); })
        .catch(function (e) { btn.disabled = false; toast(e.message, 'danger'); });
    });
  });
  document.querySelectorAll('[data-clear-key]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!confirmPress(btn)) return;
      btn.disabled = true;
      post(root.getAttribute('data-clear-key-url')).then(function () { location.reload(); })
        .catch(function (e) { btn.disabled = false; toast(e.message, 'danger'); });
    });
  });

  /* ── Visibility switches ─────────────────────────────────── */
  document.querySelectorAll('[data-visibility]').forEach(function (input) {
    input.addEventListener('change', function () {
      var kind = input.getAttribute('data-visibility');
      var on = input.checked;
      var body = kind === 'results'
        ? { exam_id: examId, show_results: on, visible: on, enabled: on }
        : { exam_id: examId, show_leaderboard: on, enabled: on };
      input.disabled = true;
      post(root.getAttribute('data-' + kind + '-url'), body)
        .then(function () {
          toast((kind === 'results' ? 'Results ' : 'Leaderboard ') + (on ? 'visible to students' : 'hidden from students'), 'success');
        })
        .catch(function (e) { input.checked = !on; toast(e.message + '. Nothing was changed.', 'danger'); })
        .then(function () { input.disabled = false; });
    });
  });

  var publish = document.querySelector('[data-publish]');
  if (publish) {
    publish.addEventListener('click', function () {
      if (!confirmPress(publish)) return;
      publish.disabled = true;
      post(root.getAttribute('data-publish-url'))
        .then(function (data) {
          var results = document.querySelector('[data-visibility="results"]');
          if (results) results.checked = true;
          toast(data.message || 'Results published.', 'success');
        })
        .catch(function (e) { toast(e.message, 'danger'); })
        .then(function () { publish.disabled = false; });
    });
  }

  /* ── Who can sit it ──────────────────────────────────────── */
  var form = document.getElementById('eligibilityForm');
  var hidden = document.getElementById('allowedStudents');
  var openPicker = document.getElementById('openPicker');
  var countText = document.getElementById('eligibilityCount');
  if (form) {
    form.querySelectorAll('input[name="access_mode"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        if (openPicker) openPicker.hidden = radio.value !== 'specific' || !radio.checked;
      });
    });
  }

  if (!picker) return;
  var boxes = Array.prototype.slice.call(picker.querySelectorAll('.nx-pick__box'));
  var cards = Array.prototype.slice.call(picker.querySelectorAll('.nx-pick'));
  var pickerCount = document.getElementById('pickerCount');

  function selectedIds() { return boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }); }
  function updateCount() { if (pickerCount) pickerCount.textContent = selectedIds().length; }
  boxes.forEach(function (b) { b.addEventListener('change', updateCount); });

  function loadFromHidden() {
    var ids = (hidden.value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    boxes.forEach(function (b) { b.checked = ids.indexOf(b.value) !== -1; });
    updateCount();
  }

  if (openPicker) {
    openPicker.addEventListener('click', function () {
      loadFromHidden();
      showTab('manual');
      if (picker.showModal) picker.showModal(); else picker.setAttribute('open', '');
      var s = document.getElementById('pickerSearch');
      if (s) s.focus();
    });
  }
  function closePicker() { if (picker.close) picker.close(); else picker.removeAttribute('open'); }
  picker.querySelectorAll('[data-picker-close]').forEach(function (b) { b.addEventListener('click', closePicker); });
  picker.addEventListener('click', function (e) { if (e.target === picker) closePicker(); });

  document.getElementById('pickerDone').addEventListener('click', function () {
    var ids = selectedIds();
    hidden.value = ids.join(',');
    if (countText) countText.textContent = ids.length + ' selected';
    closePicker();
    toast(ids.length + ' student' + (ids.length === 1 ? '' : 's') + ' selected. Press Save to apply.', 'accent');
  });

  /* Filters */
  var pSearch = document.getElementById('pickerSearch');
  var pBatch = document.getElementById('pickerBatch');
  var pRolls = document.getElementById('pickerRolls');

  function parseRolls(text) {
    text = (text || '').trim();
    if (!text) return null;
    var set = {};
    text.split(',').forEach(function (part) {
      var m = part.trim().match(/^(\d+)\s*-\s*(\d+)$/);
      if (m) {
        var a = parseInt(m[1], 10), b = parseInt(m[2], 10);
        if (b - a > 5000) return;
        for (var i = Math.min(a, b); i <= Math.max(a, b); i++) set[i] = true;
      } else if (/^\d+$/.test(part.trim())) {
        set[parseInt(part, 10)] = true;
      }
    });
    return set;
  }

  function applyFilters() {
    var q = pSearch ? pSearch.value.trim().toLowerCase() : '';
    var batch = pBatch ? pBatch.value : '';
    var rolls = parseRolls(pRolls ? pRolls.value : '');
    var shown = 0;
    cards.forEach(function (card) {
      var roll = parseInt(card.getAttribute('data-roll'), 10);
      var ok = (!q || (card.getAttribute('data-search') || '').indexOf(q) !== -1) &&
               (!batch || card.getAttribute('data-batch') === batch) &&
               (!rolls || rolls[roll]);
      card.hidden = !ok;
      if (ok) shown++;
    });
    var none = document.getElementById('pickerNoMatch');
    if (none) none.hidden = shown > 0;
  }
  [pSearch, pRolls].forEach(function (el) { if (el) el.addEventListener('input', applyFilters); });
  if (pBatch) pBatch.addEventListener('change', applyFilters);

  picker.querySelectorAll('[data-pick]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var mode = btn.getAttribute('data-pick');
      cards.forEach(function (card) {
        if (card.hidden) return;
        var box = card.querySelector('.nx-pick__box');
        var roll = parseInt(card.getAttribute('data-roll'), 10);
        if (mode === 'all') box.checked = true;
        else if (mode === 'none') box.checked = false;
        else if (mode === 'even') box.checked = !isNaN(roll) && roll % 2 === 0;
        else if (mode === 'odd') box.checked = !isNaN(roll) && roll % 2 !== 0;
      });
      updateCount();
    });
  });

  /* Tabs */
  function showTab(name) {
    picker.querySelectorAll('[data-picker-tab]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-picker-tab') === name));
    });
    picker.querySelectorAll('[data-picker-panel]').forEach(function (p) {
      p.hidden = p.getAttribute('data-picker-panel') !== name;
    });
  }
  picker.querySelectorAll('[data-picker-tab]').forEach(function (b) {
    b.addEventListener('click', function () { showTab(b.getAttribute('data-picker-tab')); });
  });

  /* ── Set generator ───────────────────────────────────────── *
   * Shuffle rolls 1..N, then place each in the smallest set that has no
   * neighbour within two rolls (falling back to the smallest set). */
  var sets = [];
  var genError = document.getElementById('genError');
  var genResults = document.getElementById('genResults');
  var genCopy = document.getElementById('genCopy');
  var pad = function (n) { return String(n).padStart(2, '0'); };

  function fail(msg) { genError.textContent = msg; genError.hidden = false; }

  function generate() {
    var total = parseInt(document.getElementById('genTotal').value, 10);
    var count = parseInt(document.getElementById('genSets').value, 10);
    var max = parseInt(document.getElementById('genMax').value, 10) || Infinity;
    genError.hidden = true;
    if (!(total >= 1)) return fail('Enter how many students are in the hall.');
    if (!(count >= 1)) return fail('Enter how many sets you need.');
    if (count > total) return fail('There cannot be more sets than students.');
    if (max < Math.ceil(total / count)) return fail('Max per set must be at least ' + Math.ceil(total / count) + '.');

    var rolls = [];
    for (var i = 1; i <= total; i++) rolls.push(i);
    for (var j = rolls.length - 1; j > 0; j--) {
      var k = Math.floor(Math.random() * (j + 1));
      var t = rolls[j]; rolls[j] = rolls[k]; rolls[k] = t;
    }
    sets = [];
    for (var s = 0; s < count; s++) sets.push([]);
    rolls.forEach(function (roll) {
      var best = -1, size = Infinity, idx;
      for (idx = 0; idx < count; idx++) {
        if (sets[idx].length >= max) continue;
        var near = sets[idx].some(function (r) { return Math.abs(r - roll) <= 2; });
        if (!near && sets[idx].length < size) { size = sets[idx].length; best = idx; }
      }
      if (best === -1) {
        for (idx = 0; idx < count; idx++) {
          if (sets[idx].length < max && sets[idx].length < size) { size = sets[idx].length; best = idx; }
        }
      }
      if (best !== -1) sets[best].push(roll);
    });
    sets.forEach(function (set) { set.sort(function (a, b) { return a - b; }); });
    render();
  }

  function render() {
    genResults.textContent = '';
    sets.forEach(function (set, i) {
      var card = document.createElement('div');
      card.className = 'nx-set';
      var head = document.createElement('div');
      head.className = 'nx-set__head';
      var title = document.createElement('b');
      title.textContent = 'Set ' + (i + 1) + ' · ' + set.length + ' students';
      var apply = document.createElement('button');
      apply.type = 'button';
      apply.className = 'nx-btn nx-btn--ghost nx-btn--sm';
      apply.innerHTML = '<i class="bi bi-check2-square" aria-hidden="true"></i><span>Select these</span>';
      apply.addEventListener('click', function () { applyRolls(set); });
      var copy = document.createElement('button');
      copy.type = 'button';
      copy.className = 'nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon';
      copy.setAttribute('aria-label', 'Copy set ' + (i + 1));
      copy.innerHTML = '<i class="bi bi-copy" aria-hidden="true"></i>';
      copy.addEventListener('click', function () {
        copyText(set.map(pad).join(', ')).then(function () { toast('Set ' + (i + 1) + ' copied', 'success'); });
      });
      head.appendChild(title); head.appendChild(apply); head.appendChild(copy);
      var list = document.createElement('div');
      list.className = 'nx-set__rolls nx-num';
      list.textContent = set.map(pad).join('  ');
      card.appendChild(head); card.appendChild(list);
      genResults.appendChild(card);
    });
    genCopy.hidden = sets.length === 0;
  }

  function applyRolls(rolls) {
    var byRoll = {};
    cards.forEach(function (card) {
      var r = parseInt(card.getAttribute('data-roll'), 10);
      if (!isNaN(r)) byRoll[r] = card.querySelector('.nx-pick__box');
    });
    boxes.forEach(function (b) { b.checked = false; });
    var matched = 0;
    rolls.forEach(function (r) { if (byRoll[r]) { byRoll[r].checked = true; matched++; } });
    updateCount();
    showTab('manual');
    if (matched < rolls.length) {
      toast(matched + ' of ' + rolls.length + ' roll numbers matched a student.', 'warning');
    } else {
      toast(matched + ' students selected', 'success');
    }
  }

  document.getElementById('genRun').addEventListener('click', generate);
  genCopy.addEventListener('click', function () {
    var text = sets.map(function (set, i) { return 'Set ' + (i + 1) + ': ' + set.map(pad).join(', '); }).join('\n');
    copyText(text).then(function () { toast('All sets copied', 'success'); });
  });
})();
