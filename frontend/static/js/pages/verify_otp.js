/*
 * Email verification
 *   - six single-digit boxes: auto-advance, backspace, paste of the whole code
 *   - resend with a 60-second cool-down
 */
(function () {
  'use strict';

  var form = document.getElementById('otpForm');
  if (!form) return;
  var boxes = Array.prototype.slice.call(document.querySelectorAll('#otpBoxes input'));
  var hidden = document.getElementById('otpValue');
  var verify = document.getElementById('verifyBtn');
  var toast = function (m, t) { if (window.nx && window.nx.toast) window.nx.toast(m, t); };

  function sync() {
    var code = boxes.map(function (b) { return b.value; }).join('');
    hidden.value = code;
    verify.disabled = code.length !== 6;
    boxes.forEach(function (b) { b.classList.toggle('is-filled', !!b.value); });
    return code;
  }

  function fill(digits, from) {
    digits.split('').forEach(function (d, k) { if (boxes[from + k]) boxes[from + k].value = d; });
    var next = Math.min(from + digits.length, boxes.length - 1);
    boxes[next].focus();
    if (sync().length === 6) form.requestSubmit ? form.requestSubmit() : form.submit();
  }

  boxes.forEach(function (box, i) {
    box.addEventListener('input', function () {
      var digits = box.value.replace(/\D/g, '');
      box.value = '';
      if (digits) fill(digits.slice(0, boxes.length - i), i);
      else sync();
    });
    box.addEventListener('keydown', function (e) {
      if (e.key === 'Backspace' && !box.value && i > 0) {
        boxes[i - 1].value = '';
        boxes[i - 1].focus();
        sync();
        e.preventDefault();
      } else if (e.key === 'ArrowLeft' && i > 0) {
        boxes[i - 1].focus();
      } else if (e.key === 'ArrowRight' && i < boxes.length - 1) {
        boxes[i + 1].focus();
      }
    });
    box.addEventListener('paste', function (e) {
      var digits = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '');
      if (!digits) return;
      e.preventDefault();
      fill(digits.slice(0, boxes.length), 0);
    });
    box.addEventListener('focus', function () { box.select(); });
  });

  form.addEventListener('submit', function (e) {
    if (sync().length !== 6) { e.preventDefault(); return; }
    verify.disabled = true;
    verify.querySelector('span').textContent = 'Verifying…';
  });

  boxes[0].focus();

  /* ── Resend ─────────────────────────────────────────────── */
  var resend = document.getElementById('resendBtn');
  var line = document.getElementById('resendLine');
  var wait = document.getElementById('resendWait');
  var timer = document.getElementById('resendTimer');

  function coolDown(seconds) {
    line.hidden = true;
    wait.hidden = false;
    timer.textContent = seconds;
    var t = setInterval(function () {
      seconds -= 1;
      timer.textContent = seconds;
      if (seconds <= 0) {
        clearInterval(t);
        wait.hidden = true;
        line.hidden = false;
        resend.disabled = false;
      }
    }, 1000);
  }

  resend.addEventListener('click', function () {
    resend.disabled = true;
    fetch(form.getAttribute('data-resend-url'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) throw new Error(d.message || d.error);
        toast('A new code is on its way. It replaces the previous one.', 'success');
        boxes.forEach(function (b) { b.value = ''; });
        sync();
        boxes[0].focus();
        coolDown(60);
      })
      .catch(function (err) {
        toast((err && err.message) || 'Could not send a new code. Please try again.', 'danger');
        resend.disabled = false;
      });
  });
})();
