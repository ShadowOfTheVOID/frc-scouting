// Timers that stop when nobody is looking.
//
// Nothing in this app used to. Six phones and two dashboards ran every interval
// they had ever started, at full rate, for a whole competition day - there was
// not one `clearInterval` in the codebase and only one `document.hidden` check.
// A backgrounded tab kept polling six endpoints every twenty seconds and
// rebuilding ten panes it was not showing.
//
// The rule here is that a hidden page does no periodic work, and gets one
// catch-up pass the moment it comes back, so it is never stale on screen. What
// must survive being hidden - a phone with unsent matches in its queue - says so
// explicitly with `whenHidden`.

/** Random 0..ms. Spreads the catch-up so eight devices returning together
 *  (a lunch break ending, a laptop lid opening) do not arrive as one burst. */
const jitter = (ms) => Math.floor(Math.random() * ms);

/**
 * An interval that pauses while the page is hidden.
 *
 * `fn` may be async; overlapping runs are suppressed, so a slow tick delays the
 * next one instead of stacking up behind it. Returns a stop function.
 *
 *   whenHidden: true   keep running in the background (use sparingly)
 *   leading:    true   run once immediately on start, and on every un-hide
 */
export function every(ms, fn, { whenHidden = false, leading = true } = {}) {
  let id = null;
  let running = false;
  let stopped = false;

  const run = async () => {
    if (running || stopped) return;
    running = true;
    try { await fn(); } catch (e) { console.error(e); } finally { running = false; }
  };

  const start = () => {
    if (id !== null || stopped) return;
    id = setInterval(run, ms);
  };
  const stop = () => { if (id !== null) { clearInterval(id); id = null; } };

  const onVisibility = () => {
    if (whenHidden) return;
    if (document.hidden) {
      stop();
    } else {
      start();
      // Coming back has to repaint, or the screen shows the moment it was
      // hidden. Jittered so a room full of devices does not land together.
      if (leading) setTimeout(run, jitter(1000));
    }
  };

  document.addEventListener('visibilitychange', onVisibility);
  if (whenHidden || !document.hidden) start();
  if (leading && !document.hidden) run();

  return () => {
    stopped = true;
    stop();
    document.removeEventListener('visibilitychange', onVisibility);
  };
}

/**
 * Trailing debounce: a burst of calls costs one run.
 *
 * The dashboard binds twelve SSE event types straight to its refresh, and the
 * hub broadcasts several of them together - a poll that lands new results
 * fires `results`, `solved` and `scout` within a few milliseconds of each
 * other, and that used to be three full six-fetch refreshes.
 */
export function coalesce(fn, ms = 750) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => { t = null; fn(...args); }, ms);
  };
}

/**
 * Calls `fn` at most once per animation frame, with the latest arguments.
 *
 * For pointermove handlers that read layout - firing those per event forces a
 * synchronous layout per mouse move, and the browser will not give you more
 * than one frame's worth of paint for it anyway.
 */
export function perFrame(fn) {
  let queued = null;
  let raf = 0;
  return (...args) => {
    queued = args;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const a = queued; queued = null;
      fn(...a);
    });
  };
}

/**
 * Fires `fn` after `ms` with no activity, and again on the first activity after.
 *
 * Used for the phone's standby screen: it stays lit all day so the scout sees
 * the match arm itself, but there is no reason for it to be *bright* while
 * nobody is touching it. `onIdle` dims, `onWake` brings it straight back.
 */
export function idleWatch(ms, { onIdle, onWake, events = ['pointerdown', 'keydown'] } = {}) {
  let t = null;
  let idle = false;
  const wake = () => {
    if (idle) { idle = false; onWake && onWake(); }
    clearTimeout(t);
    t = setTimeout(() => { idle = true; onIdle && onIdle(); }, ms);
  };
  for (const ev of events) document.addEventListener(ev, wake, { passive: true });
  wake();
  return {
    get idle() { return idle; },
    wake,
    stop() {
      clearTimeout(t);
      for (const ev of events) document.removeEventListener(ev, wake);
    },
  };
}
