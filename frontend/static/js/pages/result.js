/* Result page: filter the answer review by correct / incorrect / not answered. */
(function () {
  'use strict';
  var buttons = document.querySelectorAll('[data-review-filter]');
  var items = document.querySelectorAll('.nx-review__item');
  var none = document.getElementById('reviewNoMatch');
  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var want = btn.getAttribute('data-review-filter');
      var shown = 0;
      buttons.forEach(function (b) { b.setAttribute('aria-pressed', String(b === btn)); });
      items.forEach(function (item) {
        item.hidden = want !== 'all' && item.getAttribute('data-status') !== want;
        if (!item.hidden) shown++;
      });
      if (none) none.hidden = shown > 0;
    });
  });
})();
