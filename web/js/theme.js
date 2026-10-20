// Light or dark, for every page.
//
// Loaded as a plain script in <head>, before the stylesheets paint, so a page
// that was left on light never flashes dark on the way in. It always puts the
// theme in use on <html> as data-theme - the saved choice if there is one,
// otherwise whatever the device is set to - so theme.css only has to describe
// each theme once. A tap on any [data-theme-toggle] saves a choice for this
// browser only.
(function () {
  var KEY = 'theme';
  var root = document.documentElement;
  var light = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function saved() {
    try {
      var t = localStorage.getItem(KEY);
      return t === 'light' || t === 'dark' ? t : null;
    } catch (e) { return null; }
  }
  function resolved() {
    return saved() || (light && light.matches ? 'light' : 'dark');
  }
  function isLight() { return root.getAttribute('data-theme') === 'light'; }

  // The browser bar on a phone takes its colour from this meta, not from CSS,
  // so it is copied from the page's own panel colour once the stylesheet has it.
  function paintMeta() {
    var m = document.querySelector('meta[name="theme-color"]');
    var c = getComputedStyle(root).getPropertyValue('--panel').trim();
    if (m && c) m.setAttribute('content', c);
  }
  function paintButtons() {
    var label = isLight() ? 'Dark mode' : 'Light mode';
    var els = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < els.length; i++) {
      els[i].innerHTML = '<span class="ic" aria-hidden="true"></span>' + label;
      els[i].setAttribute('aria-label', 'Switch to ' + label.toLowerCase());
    }
  }
  function apply() {
    root.setAttribute('data-theme', resolved());
    paintMeta();
    paintButtons();
  }

  apply();
  // A device that switches itself at sunset takes the page with it, unless a
  // choice has been saved here.
  if (light && light.addEventListener) light.addEventListener('change', apply);
  document.addEventListener('DOMContentLoaded', function () { paintMeta(); paintButtons(); });
  window.addEventListener('load', paintMeta);
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-theme-toggle]');
    if (!b) return;
    try { localStorage.setItem(KEY, isLight() ? 'dark' : 'light'); } catch (err) { /* private mode */ }
    if (!saved()) root.setAttribute('data-theme', isLight() ? 'dark' : 'light');   // storage refused: this page only
    else root.setAttribute('data-theme', saved());
    paintMeta();
    paintButtons();
  });

  window.Theme = { isLight: isLight, repaint: paintButtons };
})();
