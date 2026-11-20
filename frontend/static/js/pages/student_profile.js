/* Student profile (staff view): reset the student's password in two steps,
 * then show the one-time temporary password with a copy button. */
(function () {
  'use strict';
  var root = document.getElementById('passwordReset');
  if (!root) return;
  var url = root.getAttribute('data-reset-url');
  var toast = function (msg, tone) { if (window.nx && window.nx.toast) window.nx.toast(msg, tone); };

  function step(name) {
    root.querySelectorAll('[data-reset-step]').forEach(function (el) {
      el.hidden = el.getAttribute('data-reset-step') !== name;
    });
  }

  root.querySelector('[data-reset-start]').addEventListener('click', function () {
    step('confirm');
    root.querySelector('[data-reset-confirm]').focus();
  });
  root.querySelector('[data-reset-cancel]').addEventListener('click', function () {
    step('ask');
    root.querySelector('[data-reset-start]').focus();
  });

  var confirmBtn = root.querySelector('[data-reset-confirm]');
  confirmBtn.addEventListener('click', function () {
    confirmBtn.disabled = true;
    fetch(url, { method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (data) {
        confirmBtn.disabled = false;
        if (!data.success) {
          toast(data.message || 'Could not reset the password.', 'danger');
          step('ask');
          return;
        }
        document.getElementById('tempPassword').textContent = data.temporary_password || '';
        step('done');
        root.querySelector('[data-reset-copy]').focus();
        toast('Password reset. Give the student the temporary password.', 'success');
      })
      .catch(function () {
        confirmBtn.disabled = false;
        toast('Could not reach the server. Try again.', 'danger');
      });
  });

  root.querySelector('[data-reset-copy]').addEventListener('click', function () {
    var text = document.getElementById('tempPassword').textContent;
    var done = function () { toast('Copied', 'success'); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () {});
    } else {
      var range = document.createRange();
      range.selectNodeContents(document.getElementById('tempPassword'));
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      try { document.execCommand('copy'); done(); } catch (e) {}
    }
  });
})();
