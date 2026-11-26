/* Faculty (admin/manage_faculty.html): add (showing a temporary password
 * once), reset a password, delete (two-step), search. */
(function () {
  'use strict';
  var page = document.getElementById('facultyPage');
  if (!page) return;
  var $ = function (id) { return document.getElementById(id); };
  var toast = function (m, t) { if (window.nx && window.nx.toast) window.nx.toast(m, t); };
  var confirmPress = function (b) { return window.nx && window.nx.confirmPress ? window.nx.confirmPress(b) : true; };
  var dialog = $('passwordDialog');
  var reloadAfter = false;

  function send(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || !d.success) throw new Error(d.error || 'Nothing was changed.');
        return d;
      });
    });
  }
  function showPassword(name, pw) {
    $('pwFor').textContent = 'For ' + name + ':';
    $('pwValue').textContent = pw;
    dialog.showModal ? dialog.showModal() : dialog.setAttribute('open', '');
  }
  $('pwCopy').addEventListener('click', function () {
    var text = $('pwValue').textContent;
    var done = function () { toast('Copied', 'success'); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done);
    else { var r = document.createRange(); r.selectNodeContents($('pwValue')); var s = getSelection(); s.removeAllRanges(); s.addRange(r); try { document.execCommand('copy'); done(); } catch (e) {} }
  });
  $('pwDone').addEventListener('click', function () { dialog.close(); if (reloadAfter) location.reload(); });
  dialog.addEventListener('cancel', function () { if (reloadAfter) setTimeout(function () { location.reload(); }, 0); });

  /* Search */
  var search = $('facultySearch');
  if (search) search.addEventListener('input', function () {
    var q = search.value.trim().toLowerCase();
    page.querySelectorAll('[data-faculty-row]').forEach(function (r) { r.hidden = q && r.getAttribute('data-search').indexOf(q) === -1; });
  });

  /* Reset */
  page.querySelectorAll('[data-reset]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!confirmPress(btn)) return;
      btn.disabled = true;
      send(page.getAttribute('data-reset-url').replace(/0(\/reset-password)$/, btn.getAttribute('data-reset') + '$1'))
        .then(function (d) { reloadAfter = true; showPassword(btn.getAttribute('data-name'), d.temporary_password); })
        .catch(function (e) { toast(e.message, 'danger'); })
        .then(function () { btn.disabled = false; });
    });
  });

  /* Delete */
  page.querySelectorAll('[data-delete]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!confirmPress(btn)) return;
      btn.disabled = true;
      send(page.getAttribute('data-delete-url').replace(/0$/, btn.getAttribute('data-delete')))
        .then(function (d) { btn.closest('tr').remove(); toast(d.message || 'Deleted.', 'success'); })
        .catch(function (e) { btn.disabled = false; toast(e.message, 'danger'); });
    });
  });

  /* Create */
  var form = $('createFaculty');
  var err = $('createError');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var data = {};
    new FormData(form).forEach(function (v, k) { data[k] = String(v).trim(); });
    var problems = [];
    $('f-username').classList.toggle('is-invalid', !data.username);
    $('f-email').classList.toggle('is-invalid', !/@/.test(data.email || ''));
    $('f-password').classList.toggle('is-invalid', !!data.password && data.password.length < 8);
    if (!data.username) problems.push('Enter a username.');
    if (!/@/.test(data.email || '')) problems.push('Enter a valid email.');
    if (data.password && data.password.length < 8) problems.push('A password needs at least 8 characters, or leave it blank.');
    err.hidden = !problems.length;
    err.textContent = problems.join(' ');
    if (problems.length) return;

    var btn = $('createBtn');
    btn.disabled = true;
    send(form.getAttribute('action'), data)
      .then(function (d) {
        reloadAfter = true;
        if (d.temporary_password) showPassword(data.full_name || data.username, d.temporary_password);
        else location.reload();
      })
      .catch(function (e) { err.hidden = false; err.textContent = e.message; })
      .then(function () { btn.disabled = false; });
  });
})();
