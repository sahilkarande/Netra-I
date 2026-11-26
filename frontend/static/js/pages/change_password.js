/*
 * Change password / first-login onboarding
 *   - live checklist and inline errors (no alert() dialogs)
 *   - face registration: liveness challenge, then enrolment; the server
 *     decides whether the person is live and whether to store the template
 */
(function () {
  'use strict';

  /* ── Password form ──────────────────────────────────────── */
  var form = document.getElementById('passwordChangeForm');
  if (form) {
    var oldPw = document.getElementById('old_password');
    var newPw = document.getElementById('new_password');
    var confirmPw = document.getElementById('confirm_password');
    var hint = document.getElementById('password_hint');
    var hintField = document.getElementById('hintField');
    var hintHelp = document.getElementById('hintHelp');
    var hintDefault = hintHelp.textContent;
    var hintRequired = form.getAttribute('data-hint-required') === 'true';
    var submit = document.getElementById('submitBtn');

    var setItem = function (id, ok) {
      var li = document.getElementById(id);
      li.classList.toggle('is-ok', ok);
      li.querySelector('.bi').className = 'bi ' + (ok ? 'bi-check-circle-fill' : 'bi-circle');
    };

    var problems = function () {
      var out = {};
      var h = hint.value.trim();
      if (!oldPw.value) out.old_password = 'Enter your current password.';
      if (newPw.value.length < 6) out.new_password = 'Use at least 6 characters.';
      else if (newPw.value === oldPw.value) out.new_password = 'Choose a password different from the current one.';
      if (!confirmPw.value || confirmPw.value !== newPw.value) out.confirm_password = 'The two entries do not match.';
      if (!h && hintRequired) out.password_hint = 'Add a hint so you can remember your password.';
      else if (h && newPw.value && h.toLowerCase().indexOf(newPw.value.toLowerCase()) !== -1) {
        out.password_hint = 'Your hint must not contain your password.';
      }
      return out;
    };

    var refresh = function () {
      var p = problems();
      setItem('reqLength', newPw.value.length >= 6);
      setItem('reqDifferent', !!newPw.value && !!oldPw.value && newPw.value !== oldPw.value);
      setItem('reqMatch', !!confirmPw.value && confirmPw.value === newPw.value);
      setItem('reqHint', !!hint.value.trim() && !p.password_hint);

      var hintBad = !!(p.password_hint && hint.value.trim());
      hintField.classList.toggle('is-invalid', hintBad);
      hintHelp.textContent = hintBad ? p.password_hint : hintDefault;
      return p;
    };

    var showError = function (id, message) {
      var field = document.getElementById(id).closest('.nx-field');
      field.classList.add('is-invalid');
      var help = field.querySelector('.nx-help');
      if (!help) {
        help = document.createElement('p');
        help.className = 'nx-help';
        field.appendChild(help);
      }
      help.textContent = message;
    };

    [oldPw, newPw, confirmPw, hint].forEach(function (input) {
      input.addEventListener('input', function () {
        var field = input.closest('.nx-field');
        if (input !== hint && field.classList.contains('is-invalid')) {
          field.classList.remove('is-invalid');
          var help = field.querySelector('.nx-help');
          if (help) help.remove();
        }
        refresh();
      });
    });

    form.addEventListener('submit', function (e) {
      var p = refresh();
      var ids = Object.keys(p);
      if (ids.length) {
        e.preventDefault();
        ids.forEach(function (id) { if (id !== 'password_hint' || !hint.value.trim()) showError(id, p[id]); });
        document.getElementById(ids[0]).focus();
        return;
      }
      submit.disabled = true;
      submit.querySelector('span').textContent = 'Saving…';
    });

    refresh();
  }

  /* ── Face registration ──────────────────────────────────── */
  var card = document.getElementById('faceCard');
  if (!card) return;

  var urls = {
    challenge: card.getAttribute('data-challenge-url'),
    enroll: card.getAttribute('data-enroll-url'),
    status: card.getAttribute('data-status-url')
  };
  var stage = document.getElementById('faceStage');
  var video = document.getElementById('faceVideo');
  var dots = document.getElementById('faceDots');
  var statusEl = document.getElementById('faceStatus');
  var actions = document.getElementById('faceActions');
  var continueBtn = document.getElementById('continueBtn');
  var liveness = null, running = false;

  function say(text, tone) {
    statusEl.textContent = text;
    statusEl.className = 'nx-face__status' + (tone ? ' is-' + tone : '');
  }

  function button(label, icon, variant, onClick) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'nx-btn nx-btn--' + variant;
    b.innerHTML = '<i class="bi ' + icon + '" aria-hidden="true"></i><span></span>';
    b.querySelector('span').textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  function setDots(total, done) {
    dots.hidden = !total;
    dots.replaceChildren();
    for (var i = 0; i < total; i++) {
      var d = document.createElement('span');
      if (i < done) d.className = 'is-done';
      dots.appendChild(d);
    }
  }

  function finish() {
    running = false;
    stage.classList.remove('is-on');
    dots.hidden = true;
  }

  function showRegistered(info) {
    actions.replaceChildren();
    var when = info && info.created_at ? new Date(info.created_at).toLocaleDateString() : '';
    say('Registered and locked' + (when ? ' on ' + when : '') +
        '. Ask your faculty if it ever needs to be reset.', 'ok');
    if (continueBtn) {
      continueBtn.className = 'nx-btn nx-btn--primary nx-btn--lg nx-btn--block';
      continueBtn.querySelector('span').textContent = 'Go to my dashboard';
    }
  }

  function showNotRegistered(message, tone) {
    actions.replaceChildren(button('Register my face', 'bi-camera', 'primary', start));
    say(message || 'Not registered yet.', tone);
  }

  function start() {
    if (running) return;
    if (!window.NetraFaceLiveness) {
      say('Face registration could not load on this device.', 'err');
      return;
    }
    running = true;
    stage.classList.add('is-on');
    liveness = new window.NetraFaceLiveness({
      video: video,
      onPrompt: function (text, i, total) {
        say(total ? 'Step ' + (i + 1) + ' of ' + total + ': ' + text : text);
        if (total) setDots(total, i);
      },
      onProgress: function (done, total) { setDots(total, done); }
    });
    actions.replaceChildren(button('Cancel', 'bi-x-lg', 'ghost', function () {
      if (liveness) liveness.cancel();
      finish();
      refreshStatus();
    }));

    liveness.runChallenge('enroll', urls.challenge)
      .then(function (payload) {
        finish();
        say('Checking that this is a live person…');
        return fetch(urls.enroll, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
          body: JSON.stringify(payload)
        }).then(function (r) { return r.json(); });
      }, function (err) {
        finish();
        if (err && String(err.message) === 'cancelled') { refreshStatus(); return null; }
        showNotRegistered((err && err.message) || 'The check could not be completed. Please try again.', 'err');
        return null;
      })
      .then(function (d) {
        if (!d) return;
        if (d.success || d.locked) {
          showRegistered(d.info || {});
          if (window.nx && d.success) window.nx.toast('Face registered.', 'success');
          if (d.locked && !d.success) say(d.message, 'err');
        } else {
          showNotRegistered(d.message || 'Registration failed. Please try again.', 'err');
        }
      })
      .catch(function () { finish(); showNotRegistered('Could not reach the server. Please try again.', 'err'); });
  }

  function refreshStatus() {
    fetch(urls.status, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (info) { if (info.enrolled) showRegistered(info); else showNotRegistered(); })
      .catch(function () { showNotRegistered(); });
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    say('This browser cannot use the camera, so face registration is unavailable here.', 'err');
  } else {
    refreshStatus();
  }
  window.addEventListener('beforeunload', function () { if (liveness) liveness.cancel(); });
})();
