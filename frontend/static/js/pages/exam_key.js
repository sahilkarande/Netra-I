/* Exam access-key gate (student/exam_key.html).
 * Posts the key; on success the server renders the paper for this session. */
(function () {
  var form = document.getElementById('keyForm');
  if (!form) return;
  var input = document.getElementById('keyInput');
  var field = document.getElementById('keyField');
  var error = document.getElementById('keyError');
  var btn = document.getElementById('keyBtn');
  var label = btn.querySelector('span');

  function showError(msg) {
    error.textContent = msg;
    field.classList.toggle('is-invalid', !!msg);
    input.setAttribute('aria-invalid', msg ? 'true' : 'false');
  }

  input.addEventListener('input', function () {
    var pos = input.selectionStart;
    input.value = input.value.toUpperCase();
    try { input.setSelectionRange(pos, pos); } catch (_) {}
    showError('');
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var key = input.value.trim().toUpperCase();
    if (!key) { showError('Enter the access key.'); input.focus(); return; }

    btn.disabled = true;
    label.textContent = 'Checking…';
    fetch(form.getAttribute('data-verify-url'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'access_key=' + encodeURIComponent(key)
    })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        if (data.success) { label.textContent = 'Opening…'; window.location.reload(); return; }
        showError(data.error || 'That key was not accepted. Check it and try again.');
        input.select();
        btn.disabled = false; label.textContent = 'Open the exam';
      })
      .catch(function () {
        showError('Could not reach the exam server. Check the connection and try again.');
        btn.disabled = false; label.textContent = 'Open the exam';
      });
  });
})();
