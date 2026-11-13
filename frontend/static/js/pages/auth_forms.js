/*
 * Shared behaviour for sign-in-flow forms
 *   [data-reveal="<input id>"]  show / hide a password field
 *   form[data-validate]         inline validation before submit (no browser bubbles)
 *   #prn_number + #roll_id      roll ID mirrors the PRN's last two digits
 */
(function () {
  'use strict';

  /* ── Show / hide password ───────────────────────────────── */
  document.querySelectorAll('[data-reveal]').forEach(function (btn) {
    var input = document.getElementById(btn.getAttribute('data-reveal'));
    if (!input) return;
    btn.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.setAttribute('aria-pressed', String(show));
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      btn.querySelector('.bi').className = 'bi ' + (show ? 'bi-eye-slash' : 'bi-eye');
      input.focus();
    });
  });

  /* ── Inline validation ──────────────────────────────────── */
  function labelText(input) {
    var label = document.querySelector('label[for="' + input.id + '"]');
    return label ? label.childNodes[0].textContent.trim() : 'This field';
  }

  function messageFor(input) {
    var v = input.validity;
    if (v.valueMissing) return labelText(input) + ' is required.';
    if (v.typeMismatch && input.type === 'email') return 'Enter an email address like name@example.com.';
    if (v.tooShort) return labelText(input) + ' must be at least ' + input.minLength + ' characters.';
    if (v.patternMismatch && input.id === 'prn_number') return 'PRN must be exactly 12 digits.';
    if (v.patternMismatch) return labelText(input) + ' is not in the expected format.';
    return '';
  }

  function helpFor(field) {
    var help = field.querySelector('.nx-help');
    if (!help) {
      help = document.createElement('p');
      help.className = 'nx-help';
      field.appendChild(help);
    }
    if (!help.hasAttribute('data-default')) help.setAttribute('data-default', help.textContent);
    return help;
  }

  function check(input) {
    var field = input.closest('.nx-field');
    if (!field) return true;
    var message = messageFor(input);
    var help = helpFor(field);
    field.classList.toggle('is-invalid', !!message);
    help.textContent = message || help.getAttribute('data-default');
    if (message) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
    return !message;
  }

  document.querySelectorAll('form[data-validate]').forEach(function (form) {
    var inputs = form.querySelectorAll('input:not([type="hidden"])');
    form.addEventListener('submit', function (e) {
      var firstBad = null;
      inputs.forEach(function (input) { if (!check(input) && !firstBad) firstBad = input; });
      if (firstBad) {
        e.preventDefault();
        firstBad.focus();
        return;
      }
      var btn = form.querySelector('button[type="submit"]');
      if (btn) {
        btn.disabled = true;
        var span = btn.querySelector('span');
        if (span) span.textContent = 'Please wait…';
      }
    });
    inputs.forEach(function (input) {
      input.addEventListener('blur', function () { if (input.value) check(input); });
      input.addEventListener('input', function () {
        if (input.closest('.nx-field.is-invalid')) check(input);
      });
    });
  });

  /* ── PRN → roll ID ──────────────────────────────────────── */
  var prn = document.getElementById('prn_number');
  var roll = document.getElementById('roll_id');
  if (prn && roll) {
    var help = document.getElementById('prnHelp');
    prn.addEventListener('input', function () {
      prn.value = prn.value.replace(/\D/g, '').slice(0, 12);
      roll.value = prn.value.length >= 2 ? prn.value.slice(-2) : '';
      if (help && !prn.closest('.nx-field.is-invalid')) {
        help.textContent = prn.value.length >= 2
          ? 'Roll ID will be ' + roll.value + '.'
          : 'Roll ID is taken from the last two digits.';
        help.setAttribute('data-default', help.textContent);
      }
    });
  }
})();
