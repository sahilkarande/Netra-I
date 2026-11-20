/*
 * Exam analytics (faculty/analytics.html)
 *   - student search and All / Flagged / To review filter
 *   - proctoring review dialog: summary, accept / invalidate with a note,
 *     timeline with evidence images, delete one image
 *   - publish results (two-step)
 * Every value from the server is set with textContent or as an attribute,
 * never parsed as HTML.
 */
(function () {
  'use strict';

  var root = document.getElementById('analytics');
  var toast = function (msg, tone) { if (window.nx && window.nx.toast) window.nx.toast(msg, tone); };
  var confirmPress = function (btn) { return window.nx && window.nx.confirmPress ? window.nx.confirmPress(btn) : true; };
  function urlFor(attr, id) { return root.getAttribute(attr).replace(/0$/, String(id)); }

  function send(url, method, body) {
    var opts = { method: method, headers: { 'X-Requested-With': 'XMLHttpRequest' } };
    if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok || data.success === false) throw new Error(data.error || data.message || 'Something went wrong');
        return data;
      });
    });
  }

  /* ── Publish ─────────────────────────────────────────────── */
  var publish = document.getElementById('publishResults');
  if (publish) {
    publish.addEventListener('click', function () {
      if (!confirmPress(publish)) return;
      publish.disabled = true;
      send(publish.getAttribute('data-url'), 'POST')
        .then(function () { location.reload(); })
        .catch(function (e) { publish.disabled = false; toast(e.message, 'danger'); });
    });
  }

  if (!root) return;

  /* ── Search and filter ───────────────────────────────────── */
  var search = document.getElementById('studentSearch');
  var mode = 'all';
  function apply() {
    var q = search ? search.value.trim().toLowerCase() : '';
    var shown = 0;
    document.querySelectorAll('[data-row]').forEach(function (row) {
      var ok = (!q || (row.getAttribute('data-search') || '').indexOf(q) !== -1) &&
               (mode === 'all' || row.getAttribute('data-' + mode) === '1');
      row.hidden = !ok;
      if (ok) shown++;
    });
    var none = document.getElementById('rowNoMatch');
    if (none) none.hidden = shown > 0;
  }
  function setMode(next) {
    mode = next;
    document.querySelectorAll('[data-row-filter]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-row-filter') === mode));
    });
    apply();
  }
  if (search) search.addEventListener('input', apply);
  document.querySelectorAll('[data-row-filter]').forEach(function (b) {
    b.addEventListener('click', function () { setMode(b.getAttribute('data-row-filter')); });
  });
  document.querySelectorAll('[data-filter-link]').forEach(function (a) {
    a.addEventListener('click', function () { setMode(a.getAttribute('data-filter-link')); });
  });

  /* ── Review dialog ───────────────────────────────────────── */
  var dialog = document.getElementById('reviewDialog');
  var current = null;
  var note = document.getElementById('reviewNote');
  var noteField = document.getElementById('noteField');
  var noteHelp = document.getElementById('noteHelp');

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function badge(label, tone) { return el('span', 'nx-badge nx-badge--' + tone, label); }

  function open(id, name) {
    current = id;
    document.getElementById('reviewTitle').textContent = name;
    document.getElementById('reviewSub').textContent = 'Proctoring review';
    document.getElementById('reviewLoading').hidden = false;
    document.getElementById('reviewBody').hidden = true;
    note.value = ''; noteField.classList.remove('is-invalid'); noteHelp.textContent = '';
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
    send(urlFor('data-status-url', id), 'GET').then(render).catch(function (e) {
      document.getElementById('reviewLoading').textContent = e.message;
    });
  }

  function render(data) {
    document.getElementById('reviewLoading').hidden = true;
    document.getElementById('reviewBody').hidden = false;

    var summary = document.getElementById('reviewSummary');
    summary.textContent = '';
    [['Flags', String(data.total_violations || 0)],
     ['Proctoring', String(data.proctoring_status || 'active')],
     ['Calibration', data.calibration_completed ? 'Done' : 'Not done']].forEach(function (pair) {
      var box = el('div', 'nx-review-summary__item');
      box.appendChild(el('span', 'nx-review-summary__label', pair[0]));
      box.appendChild(el('b', null, pair[1]));
      summary.appendChild(box);
    });
    var decision = el('div', 'nx-review-summary__item');
    decision.appendChild(el('span', 'nx-review-summary__label', 'Decision'));
    if (data.is_invalidated) decision.appendChild(badge('Invalidated', 'danger'));
    else if (data.faculty_review_note) decision.appendChild(badge('Accepted', 'success'));
    else decision.appendChild(badge('Not reviewed', 'warning'));
    if (data.faculty_review_note) decision.appendChild(el('span', 'nx-help', data.faculty_review_note));
    summary.appendChild(decision);

    var list = document.getElementById('reviewTimeline');
    list.textContent = '';
    var items = data.recent_violations || [];
    document.getElementById('reviewEmpty').hidden = items.length > 0;
    items.forEach(function (v) {
      var li = el('li', 'nx-timeline__item nx-tone-' + (v.severity === 'high' ? 'danger' : 'warning'));
      var head = el('div', 'nx-timeline__head');
      head.appendChild(el('b', null, String(v.type || 'Flag').replace(/_/g, ' ')));
      head.appendChild(el('span', 'nx-num nx-help', v.timestamp ? new Date(v.timestamp).toLocaleTimeString() : ''));
      li.appendChild(head);
      if (v.message) li.appendChild(el('p', 'nx-help', v.message));
      if (v.evidence_url) {
        var fig = el('div', 'nx-evidence');
        var link = el('a'); link.href = v.evidence_url; link.target = '_blank'; link.rel = 'noopener';
        var img = el('img'); img.src = v.evidence_url; img.alt = 'Evidence for ' + (v.type || 'flag'); img.loading = 'lazy';
        link.appendChild(img); fig.appendChild(link);
        var del = el('button', 'nx-btn nx-btn--ghost nx-btn--sm');
        del.type = 'button';
        del.setAttribute('data-nx-confirm', 'Delete image?');
        del.innerHTML = '<i class="bi bi-trash3" aria-hidden="true"></i><span>Delete image</span>';
        del.addEventListener('click', function () {
          if (!confirmPress(del)) return;
          del.disabled = true;
          send(urlFor('data-evidence-url', Number(v.id)), 'DELETE')
            .then(function () { fig.remove(); toast('Image deleted', 'success'); })
            .catch(function (e) { del.disabled = false; toast(e.message, 'danger'); });
        });
        fig.appendChild(del);
        li.appendChild(fig);
      }
      list.appendChild(li);
    });
  }

  document.querySelectorAll('[data-review-open]').forEach(function (b) {
    b.addEventListener('click', function () { open(b.getAttribute('data-review-open'), b.getAttribute('data-name')); });
  });
  dialog.querySelectorAll('[data-review-close]').forEach(function (b) {
    b.addEventListener('click', function () { dialog.close ? dialog.close() : dialog.removeAttribute('open'); });
  });
  dialog.addEventListener('click', function (e) { if (e.target === dialog) dialog.close(); });

  dialog.querySelectorAll('[data-decision]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var action = btn.getAttribute('data-decision');
      var text = note.value.trim();
      if (action === 'invalidate' && !text) {
        noteField.classList.add('is-invalid');
        noteHelp.textContent = 'Say why you are invalidating this attempt.';
        note.focus();
        return;
      }
      if (btn.hasAttribute('data-nx-confirm') && !confirmPress(btn)) return;
      btn.disabled = true;
      send(urlFor('data-review-url', current), 'POST', { action: action, note: text })
        .then(function () { location.reload(); })
        .catch(function (e) { btn.disabled = false; toast(e.message, 'danger'); });
    });
  });
  note.addEventListener('input', function () { noteField.classList.remove('is-invalid'); noteHelp.textContent = ''; });
})();
