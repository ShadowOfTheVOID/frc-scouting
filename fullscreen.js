// Getting rid of the browser chrome, which differs by platform:
//
//   iOS      Add to Home Screen gives a true standalone window with no URL bar.
//            Works over plain HTTP. The Fullscreen API does NOT exist on iPhone
//            Safari, so Home Screen is the only route there.
//   Android  Chrome only builds a real standalone app (WebAPK) from a secure
//            origin, and we serve HTTP over the LAN. "Add to home screen" there
//            makes a shortcut that still opens in a tab with the URL bar.
//            The Fullscreen API, however, works fine over HTTP on a user
//            gesture - so that is what we use.
//
// Net effect: both platforms end up chrome-free, by different means.

export const isiOS = /iP(hone|ad|od)/.test(navigator.platform) ||
  (navigator.userAgent.includes('Mac') && 'ontouchend' in document);

export function isStandalone() {
  return window.navigator.standalone === true ||
    window.matchMedia('(display-mode: standalone)').matches ||
    window.matchMedia('(display-mode: fullscreen)').matches;
}

export function canFullscreen() {
  const el = document.documentElement;
  return !!(el.requestFullscreen || el.webkitRequestFullscreen);
}

export function isFullscreen() {
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

/** True once the browser chrome is gone, by whichever route. */
export function isImmersive() {
  return isStandalone() || isFullscreen();
}

export async function enter({ orientation } = {}) {
  const el = document.documentElement;
  try {
    if (el.requestFullscreen) await el.requestFullscreen({ navigationUI: 'hide' });
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  } catch { /* user declined, or not permitted - never fatal */ }
  if (orientation && screen.orientation && screen.orientation.lock) {
    try { await screen.orientation.lock(orientation); } catch { /* desktop / unsupported */ }
  }
}

/**
 * Wire up chrome-hiding.
 *
 * `triggers` are selectors whose first tap also enters fullscreen — so the
 * scout never has to think about it, it just happens when they sit down or
 * start a match. A hint is shown only where it is actually actionable.
 */
export function install({ triggers = [], orientation, hintEl } = {}) {
  if (isStandalone()) { if (hintEl) hintEl.remove(); return; }

  const go = () => { if (!isFullscreen()) enter({ orientation }); };

  if (canFullscreen()) {
    for (const sel of triggers) {
      const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
      if (el) el.addEventListener('pointerdown', go, { once: false, passive: true });
    }
    // A system dialog or a rotation can drop us out; step back in on the next tap.
    document.addEventListener('fullscreenchange', () => {
      if (!isFullscreen()) document.addEventListener('pointerdown', go, { once: true, passive: true });
    });
  }

  if (hintEl) {
    if (canFullscreen()) {
      hintEl.textContent = 'TAP FOR FULL SCREEN';
      hintEl.addEventListener('click', go);
    } else if (isiOS) {
      // iPhone Safari: no Fullscreen API, so tell them the one thing that works.
      hintEl.textContent = 'SHARE → ADD TO HOME SCREEN';
    } else {
      hintEl.remove();
    }
  }
}

/**
 * Offer "Add to Home Screen" — iPhone only, and only while still in Safari.
 *
 * This is not cosmetic. iOS gives a home-screen web app its own storage
 * partition, so IndexedDB written in the Safari tab is NOT visible to the
 * installed app. A scout who logs two matches and *then* installs leaves that
 * data stranded in a tab nobody opens again. So the ask has to land before the
 * first match, which is why this runs on the seat screen and says so plainly.
 *
 * Android is deliberately excluded: over plain HTTP Chrome only makes a
 * shortcut, not a WebAPK, so `enter()` above is the better route there.
 */
export function offerHomeScreen({ el, skipKey = 'a2hsSkipped' } = {}) {
  if (!el) return;
  const pointless = !isiOS || isStandalone() || localStorage.getItem(skipKey) === '1';
  if (pointless) { el.remove(); return; }

  el.classList.remove('hide');
  const dismiss = () => { localStorage.setItem(skipKey, '1'); el.remove(); };
  el.querySelector('#a2hsSkip').addEventListener('click', dismiss);
}
