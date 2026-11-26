/* Edit student: delete the account (two-step). The password reset panel is
 * handled by student_profile.js, which this page also loads. */
(function () {
  'use strict';
  var panel = document.getElementById('deleteStudent');
  if (!panel) return;
  var btn = panel.querySelector('[data-delete-student]');
  var toast = function (msg, tone) { if (window.nx && window.nx.toast) window.nx.toast(msg, tone); };
  btn.addEventListener('click', function () {
    if (window.nx && window.nx.confirmPress && !window.nx.confirmPress(btn)) return;
    btn.disabled = true;
    fetch(panel.getAttribute('data-delete-url'), { method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok || !d.success) throw new Error(d.error || 'Nothing was changed.'); }); })
      .then(function () { window.location.href = panel.getAttribute('data-done-url'); })
      .catch(function (e) { btn.disabled = false; toast(e.message, 'danger'); });
  });
})();
