/*
 * Sign-in page
 *   - show / hide password, Caps Lock warning
 *   - password hint lookup (server returns one generic message for unknown accounts)
 *   - face sign-in: offered only for accounts with a registered face; the
 *     liveness challenge and the identity match are both decided by the server
 */
(function () {
  'use strict';

  var form = document.getElementById('loginForm');
  if (!form) return;
  var urls = {
    hint: form.getAttribute('data-hint-url'),
    faceAvailable: form.getAttribute('data-face-available-url'),
    faceChallenge: form.getAttribute('data-face-challenge-url'),
    faceLogin: form.getAttribute('data-face-login-url')
  };
  var idField = document.getElementById('username');
  var password = document.getElementById('password');
  var submit = document.getElementById('loginBtn');

  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  /* ── Show / hide password ───────────────────────────────── */
  var toggle = document.getElementById('togglePassword');
  toggle.addEventListener('click', function () {
    var show = password.type === 'password';
    password.type = show ? 'text' : 'password';
    toggle.setAttribute('aria-pressed', String(show));
    toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    toggle.querySelector('.bi').className = 'bi ' + (show ? 'bi-eye-slash' : 'bi-eye');
    password.focus();
  });

  /* ── Caps Lock ──────────────────────────────────────────── */
  var caps = document.getElementById('capsWarning');
  function checkCaps(e) {
    if (e.getModifierState) caps.hidden = !e.getModifierState('CapsLock');
  }
  password.addEventListener('keydown', checkCaps);
  password.addEventListener('keyup', checkCaps);
  password.addEventListener('blur', function () { caps.hidden = true; });

  /* ── Submit: inline validation, then a busy state ───────── */
  form.addEventListener('submit', function (e) {
    var missing = !idField.value.trim() ? idField : (!password.value ? password : null);
    if (missing) {
      e.preventDefault();
      missing.closest('.nx-field').classList.add('is-invalid');
      missing.focus();
      return;
    }
    submit.disabled = true;
    submit.querySelector('span').textContent = 'Signing in…';
  });
  [idField, password].forEach(function (input) {
    input.addEventListener('input', function () { input.closest('.nx-field').classList.remove('is-invalid'); });
  });

  /* ── Password hint ──────────────────────────────────────── */
  var hintLink = document.getElementById('showHintLink');
  var hintBox = document.getElementById('hintBox');
  var hintText = document.getElementById('hintText');

  function showHint(message, isHint) {
    hintText.textContent = '';
    if (isHint) {
      var label = document.createElement('b');
      label.textContent = 'Your hint: ';
      hintText.appendChild(label);
    }
    hintText.appendChild(document.createTextNode(message));   // user-written text: never innerHTML
    hintBox.hidden = false;
  }

  hintLink.addEventListener('click', function () {
    var id = idField.value.trim();
    if (!id) {
      showHint('Enter your username, email or PRN first.', false);
      idField.focus();
      return;
    }
    hintLink.disabled = true;
    postJSON(urls.hint, { username: id })
      .then(function (d) { showHint(d.success ? d.hint : (d.message || 'No hint is saved for that account.'), !!d.success); })
      .catch(function () { showHint('Could not load your hint. Please try again.', false); })
      .then(function () { hintLink.disabled = false; });
  });

  /* ── Face sign-in ───────────────────────────────────────── */
  var block = document.getElementById('faceBlock');
  var stage = document.getElementById('faceCamWrap');
  var video = document.getElementById('faceLoginVideo');
  var msg = document.getElementById('faceMsg');
  var btn = document.getElementById('faceLoginBtn');
  var defaultMsg = msg.textContent;
  var liveness = null, busy = false, lastChecked = null;

  function say(text, tone) {
    msg.textContent = text;
    msg.className = 'nx-face__status' + (tone ? ' is-' + tone : '');
  }

  function offerFace() {
    var id = idField.value.trim();
    if (id === lastChecked) return;
    lastChecked = id;
    if (!id || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.NetraFaceLiveness) {
      block.hidden = true;
      return;
    }
    postJSON(urls.faceAvailable, { username: id })
      .then(function (d) {
        block.hidden = !d.available;
        if (d.available) say(defaultMsg);
      })
      .catch(function () { block.hidden = true; });
  }

  function reset() {
    busy = false;
    btn.disabled = false;
    stage.classList.remove('is-on');
  }

  btn.addEventListener('click', function () {
    if (busy) return;
    var id = idField.value.trim();
    if (!id) { say('Enter your username, email or PRN first.', 'err'); idField.focus(); return; }

    busy = true;
    btn.disabled = true;
    stage.classList.add('is-on');
    liveness = new window.NetraFaceLiveness({
      video: video,
      onPrompt: function (text, i, total) {
        say(total ? 'Step ' + (i + 1) + ' of ' + total + ': ' + text : text);
      }
    });

    liveness.runChallenge('login', urls.faceChallenge)
      .then(function (payload) {
        stage.classList.remove('is-on');
        say('Checking that this is a live person…');
        return postJSON(urls.faceLogin, { username: id, nonce: payload.nonce, steps: payload.steps });
      }, function (err) {
        reset();
        say((err && err.message) || 'The check could not be completed. Use your password instead.', 'err');
        return null;
      })
      .then(function (d) {
        if (!d) return;
        if (d.success && d.redirect) {
          say('Verified. Signing you in…', 'ok');
          window.location.href = d.redirect;
          return;
        }
        reset();
        say(d.message || 'Face not recognised. Try again or use your password.', 'err');
        if (d.redirect) window.location.href = d.redirect;
      })
      .catch(function () {
        reset();
        say('Could not reach the server. Use your password instead.', 'err');
      });
  });

  idField.addEventListener('change', offerFace);
  idField.addEventListener('blur', offerFace);
  if (idField.value.trim()) offerFace();
  window.addEventListener('beforeunload', function () { if (liveness) liveness.cancel(); });
})();
