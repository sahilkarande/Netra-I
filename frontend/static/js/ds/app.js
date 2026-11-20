/*
 * Netra-I design system · app shell behaviour
 *   - theme menu (system / light / dark), stored under "netra-theme"
 *   - dropdown menus  [data-nx-menu-toggle="<menu id>"]
 *   - mobile drawer   [data-nx-drawer-toggle]
 *   - toasts          window.nx.toast(message, tone)
 * No dependencies. Loaded at the end of layouts/app.html.
 */
(function () {
  'use strict';

  var THEME_KEY = 'netra-theme';
  var root = document.documentElement;
  var nx = window.nx = window.nx || {};

  /* ── Theme ─────────────────────────────────────────────── */
  function readTheme() {
    try {
      var v = localStorage.getItem(THEME_KEY);
      return v === 'light' || v === 'dark' ? v : 'system';
    } catch (e) { return 'system'; }
  }

  function applyTheme(mode) {
    if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
    else root.removeAttribute('data-theme');
    try {
      if (mode === 'system') localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, mode);
    } catch (e) { /* storage blocked: the choice lasts for this page only */ }

    document.querySelectorAll('[data-nx-theme]').forEach(function (item) {
      item.setAttribute('aria-checked', String(item.getAttribute('data-nx-theme') === mode));
    });
    var icon = document.querySelector('[data-nx-theme-icon]');
    if (icon) {
      icon.className = 'bi ' + ({ light: 'bi-sun', dark: 'bi-moon-stars', system: 'bi-circle-half' })[mode];
    }
    document.dispatchEvent(new CustomEvent('nx:themechange', { detail: { mode: mode } }));
  }
  nx.setTheme = applyTheme;

  document.querySelectorAll('[data-nx-theme]').forEach(function (item) {
    item.addEventListener('click', function () {
      applyTheme(item.getAttribute('data-nx-theme'));
      closeMenus();
    });
  });
  applyTheme(readTheme());

  /* ── Menus ─────────────────────────────────────────────── */
  function closeMenus(except) {
    document.querySelectorAll('[data-nx-menu-toggle][aria-expanded="true"]').forEach(function (btn) {
      if (btn === except) return;
      btn.setAttribute('aria-expanded', 'false');
      var menu = document.getElementById(btn.getAttribute('data-nx-menu-toggle'));
      if (menu) menu.classList.remove('is-open');
    });
  }

  document.querySelectorAll('[data-nx-menu-toggle]').forEach(function (btn) {
    var menu = document.getElementById(btn.getAttribute('data-nx-menu-toggle'));
    if (!menu) return;
    btn.setAttribute('aria-haspopup', 'menu');
    btn.setAttribute('aria-expanded', 'false');

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = btn.getAttribute('aria-expanded') === 'true';
      closeMenus(btn);
      btn.setAttribute('aria-expanded', String(!open));
      menu.classList.toggle('is-open', !open);
      if (!open) {
        var first = menu.querySelector('[role="menuitem"], [role="menuitemradio"]');
        if (first) first.focus();
      }
    });

    menu.addEventListener('keydown', function (e) {
      var items = Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"], [role="menuitemradio"]'));
      var i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      if (e.key === 'Escape') { closeMenus(); btn.focus(); }
    });
  });

  document.addEventListener('click', function (e) {
    if (!e.target.closest('.nx-menu')) closeMenus();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeMenus(); closeDrawer(); }
  });

  /* ── Drawer (sidebar on narrow screens) ────────────────── */
  var app = document.querySelector('.nx-app');
  function closeDrawer() {
    if (!app || !app.classList.contains('is-drawer-open')) return;
    app.classList.remove('is-drawer-open');
    document.querySelectorAll('[data-nx-drawer-toggle]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
  }
  document.querySelectorAll('[data-nx-drawer-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var open = app.classList.toggle('is-drawer-open');
      btn.setAttribute('aria-expanded', String(open));
      if (open) {
        var current = app.querySelector('.nx-sidebar [aria-current="page"]') ||
                      app.querySelector('.nx-sidebar .nx-nav__item');
        if (current) current.focus();
      }
    });
  });
  var scrim = document.querySelector('.nx-scrim');
  if (scrim) scrim.addEventListener('click', closeDrawer);

  /* ── Toasts ────────────────────────────────────────────── */
  var ICONS = { success: 'bi-check-circle-fill', warning: 'bi-exclamation-triangle-fill',
                danger: 'bi-x-circle-fill', accent: 'bi-info-circle-fill' };

  function dismiss(toast) {
    if (!toast || toast.classList.contains('is-leaving')) return;
    toast.classList.add('is-leaving');
    setTimeout(function () { toast.remove(); }, 200);
  }

  function arm(toast, tone) {
    var close = toast.querySelector('.nx-toast__close');
    if (close) close.addEventListener('click', function () { dismiss(toast); });
    if (tone !== 'danger') setTimeout(function () { dismiss(toast); }, 5000);
  }

  nx.toast = function (message, tone) {
    tone = ICONS[tone] ? tone : 'accent';
    var region = document.querySelector('.nx-toasts');
    if (!region) return;
    var toast = document.createElement('div');
    toast.className = 'nx-toast nx-tone-' + tone;
    toast.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
    toast.innerHTML = '<i class="bi ' + ICONS[tone] + '" aria-hidden="true"></i>' +
      '<div class="nx-toast__text"></div>' +
      '<button type="button" class="nx-toast__close" aria-label="Dismiss">&times;</button>';
    toast.querySelector('.nx-toast__text').textContent = message;
    region.appendChild(toast);
    arm(toast, tone);
    return toast;
  };

  document.querySelectorAll('.nx-toasts .nx-toast').forEach(function (t) {
    arm(t, t.getAttribute('data-tone'));
  });

  /* ── Two-step confirmation (instead of window.confirm) ───────
     nx.confirmPress(button): the first press arms the button (it turns red
     and its label becomes data-nx-confirm), the second press within 4 s
     returns true. Forms with a data-nx-confirm submit button are wired
     automatically. */
  nx.confirmPress = function (btn) {
    if (btn.hasAttribute('data-armed')) { disarm(btn); return true; }
    var label = btn.querySelector('span') || btn;
    btn.setAttribute('data-armed', '');
    btn.setAttribute('data-label', label.textContent);
    btn.setAttribute('data-variant', btn.className);
    label.textContent = btn.getAttribute('data-nx-confirm') || 'Press again to confirm';
    btn.classList.remove('nx-btn--ghost', 'nx-btn--secondary', 'nx-btn--primary');
    btn.classList.add('nx-btn--danger');
    btn._nxTimer = setTimeout(function () { disarm(btn); }, 4000);
    return false;
  };
  function disarm(btn) {
    if (!btn.hasAttribute('data-armed')) return;
    clearTimeout(btn._nxTimer);
    (btn.querySelector('span') || btn).textContent = btn.getAttribute('data-label');
    btn.className = btn.getAttribute('data-variant');
    btn.removeAttribute('data-armed');
  }
  document.querySelectorAll('form').forEach(function (form) {
    var btn = form.querySelector('button[type="submit"][data-nx-confirm]');
    if (!btn) return;
    form.addEventListener('submit', function (e) {
      if (!nx.confirmPress(btn)) e.preventDefault();
    });
    btn.addEventListener('blur', function () { setTimeout(function () { if (document.activeElement !== btn) disarm(btn); }, 150); });
  });
})();
