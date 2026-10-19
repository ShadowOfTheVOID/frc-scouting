// Light or dark, for every page.
//
// Loaded as a plain script in <head>, before the stylesheet paints, so a page
// that was left on light never flashes dark on the way in. With nothing saved
// the page follows the device (theme.css reads prefers-color-scheme); a tap on
// any [data-theme-toggle] saves a choice for this browser only.
(function () {
  var KEY = 'theme';
  var root = document.documentElement;
  var light = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function isLight() {
    var t = root.getAttribute('data-theme');
    if (t) return t === 'light';
    return !!(light && light.matches);
  }
  // The browser bar on a phone takes its colour from this meta, not from CSS.
  function paintMeta() {
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute('content', isLight() ? '#fbf6e4' : '#1a1a1e');
  }
  function paintButtons() {
    var label = isLight() ? 'Dark mode' : 'Light mode';
    var els = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < els.length; i++) {
      els[i].innerHTML = '<span class="ic" aria-hidden="true"></span>' + label;
      els[i].setAttribute('aria-label', 'Switch to ' + label.toLowerCase());
    }
  }
  function apply(t) {
    if (t === 'light' || t === 'dark') root.setAttribute('data-theme', t);
    else root.removeAttribute('data-theme');
    paintMeta();
    paintButtons();
  }

  apply(saved());
  if (light && light.addEventListener) light.addEventListener('change', function () { apply(saved()); });
  document.addEventListener('DOMContentLoaded', function () { paintMeta(); paintButtons(); });
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-theme-toggle]');
    if (!b) return;
    var next = isLight() ? 'dark' : 'light';
    try { localStorage.setItem(KEY, next); } catch (err) { /* private mode: this page only */ }
    apply(next);
  });

  window.Theme = { isLight: isLight, repaint: paintButtons };
})();
