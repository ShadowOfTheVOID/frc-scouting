# FRC Scouting UI — Implementation Handoff

**Read this whole file before writing any code.**

These 12 HTML files are not inspiration. They are the specification. Build the app so that a
screenshot of your build and a screenshot of the corresponding file are indistinguishable at
1x. Every pixel value, hex code, font weight, letter-spacing, and border width in these files
is a decision. Reproduce them exactly.

---

## The rule

> **Do not invent a single value.**

If you need a color, a padding, a font size, a radius, a gap, a border, or a shadow: open the
relevant `.dc.html`, find the element, and copy the value verbatim. If you cannot find a value
in the source files, **stop and ask** — do not fill the gap with your own judgment, a Tailwind
default, a shadcn component, or a "close enough" token.

Specifically forbidden without asking first:

- Substituting a component library (shadcn, MUI, Chakra, Radix presets, DaisyUI) for the
  hand-built markup here. These screens use no component library and no framework CSS.
- Rounding values to a scale. `padding: 11px 16px` is not `p-3`. `9.5px` font size is not
  `text-xs`. `letter-spacing: .16em` is not `tracking-wider`. If your styling system cannot
  express the exact value, use inline styles or a custom CSS property, not the nearest token.
- Changing any color. Not one hex digit. There is no "close enough" red.
- Changing fonts, weights, or fallbacks.
- Adding shadows, gradients, transitions, hover effects, animations, focus rings, icons,
  emoji, or rounded corners that are not in the source.
- Removing or reordering anything. Every table column, badge, caption, footnote, and grey
  helper line stays, in the same order, with the same copy.
- Rewriting copy. Labels are cased and abbreviated deliberately (`RECONCILE`, `nobody seated`,
  `stale · 6m`). Ship them character-for-character, including the `·` middot separators, the
  `−` minus sign in negative bias values, and the `✕ ▢ —` window glyphs.
- "Improving" layout, spacing, hierarchy, responsiveness, or accessibility on your own
  initiative. Note the suggestion in a comment and move on.

## Exact first, then expandable — in that order

Two requirements that sound opposed. They are not, and the order matters.

**1. Exact at the reference size.** At 844 × 390 (phone) and 1240px (computer) your build must be
pixel-identical to the file. Every value verbatim. This is the acceptance test — screenshot both,
compare, fix every visible difference. Do this before you think about any other screen size.

**2. Then make it expand per device screen.** Nothing may be hardcoded to one device. The same
screen must fill an iPhone SE and an iPhone 16 Pro Max, a Galaxy S23 and a Pixel 9, and the
dashboard must survive a 1280px laptop and a 2560px monitor. Expanding is NOT re-designing — it
is stating explicitly which values are fixed and which absorb space:

Phone screens
- Fixed: the side columns (218px and 186px), the 44px header bar, the safe-area insets, all
  padding, all radii, all font sizes, all border widths, all gaps.
- Absorbs: the centre column, via `1fr`. It takes every extra or missing pixel.
- Station buttons stay a 3 × 2 grid at all widths. Never a single row of six.
- Vertically: the tall panels are `flex:1`. Fixed heights are floors, written as `min-height`,
  never `height`.
- Nothing wraps, nothing scrolls, nothing collapses to a hamburger during a match. If the screen
  is too small to hold the layout, it is too small to scout on — say so, don't reflow.

Computer screens
- Fixed: 1240px is the *minimum* content width and the design canvas, not a maximum. Right rails
  keep their stated widths (360px on the server console). Table column widths hold.
- Absorbs: the main left column and any `1fr` table column.
- Above 1240px the layout centres and the main column grows; do not stretch the rails, do not
  scale type, do not add a max-width that letterboxes the content at 1440px.
- Below 1240px: horizontal scroll. Do not collapse the grid.

The test for whether you got this right: resize the window continuously from the smallest to the
largest supported size and nothing jumps, reflows, or clips — only the `1fr` regions change.
Anything else changing is a bug.

## How to work

1. Open the file. Read the actual markup — not a screenshot of it.
2. Copy the style attribute values across into your components literally. The fastest correct
   path is to port each screen's markup 1:1 first, then extract shared pieces afterward — not
   to design an abstraction and hope the values fall out of it.
3. Diff visually. Render your build at the exact canvas size, screenshot it, screenshot the
   source file, and compare. Fix every difference you can see.
4. Only after a screen matches pixel-for-pixel, refactor internals — and re-diff after.

## Canvas sizes (exact)

| Surface  | Size            | Notes |
|----------|-----------------|-------|
| Phone    | 844 × 390 CSS px reference, fluid 780–932 | Landscape, forced, iOS + Android. The device bezel in the mock files is presentation only — do not build the bezel. |
| Computer | 1240 px wide, height by content | Fixed width, not fluid. The window chrome bar in the mocks IS part of the design for the server console (Windows) and is presentation only for the dashboard (macOS traffic lights) — dashboard chrome is not built. |

Phone screens are landscape because the app is a two-thumb game controller. Do not add a
portrait layout. Do not add a mobile breakpoint to the computer screens. There is no tablet.

## Platform compatibility — iOS and Android, one layout

The phone app ships on both. There is **one** layout, not two. It fits inside a shared safe box
whose insets are the worst case of either platform. `Phone Platform Spec.dc.html` is the
drawing; these are the numbers:

| Edge | Value | Why |
|------|-------|-----|
| Leading | `max(env(safe-area-inset-left), 44px)` | iOS Dynamic Island in landscape (44) beats Android punch-hole (32) |
| Trailing | `max(env(safe-area-inset-right), 48px)` | Android 3-button nav bar (48) beats iOS home indicator (34) |
| Top | 54px | 44px header bar + 10px gap |
| Bottom | 20px | clears both gesture strips |

All five phone mock files already use these insets (content stops at 44px / 48px). Measure the
files.

Rules:

- The header bar spans edge to edge; only its `padding: 0 74px` keeps text off the cutout.
  Do not inset the bar — the background must reach the physical edge.
- Locked landscape, both rotations. No portrait layout. No tablet layout.
- Android: `layoutInDisplayCutoutMode = shortEdges`, draw edge to edge, consume insets in code.
- iOS: hide the status bar in landscape, `viewport-fit=cover`.
- No horizontal edge drags anywhere — Android's back gesture owns both vertical edges. The rate
  ladder and hold meter are tap and hold, as drawn.
- Keep-awake for the whole match: wake lock on Android, idle-timer disable on iOS; released at
  the buzzer.
- Haptics: one short tick per rate change and meter commit. iOS light impact, Android 12ms
  one-shot. Never a long buzz.
- Test at 667×375, 780×360, 844×390 (reference), 851×393, 932×430. Fixed side columns hold
  their widths; the centre column absorbs the difference. Nothing wraps during a match.

## Type

Load from Google Fonts, these families only:

- `Barlow` — 400, 500, 600, 700, 800. UI text.
- `Barlow Condensed` — 500, 600, 700. Large numerals and match codes only.
- `JetBrains Mono` — 400, 500. Log lines, PIDs, IPs, device build strings only.

Fallback stack as written in the files: `Barlow, system-ui, sans-serif`. Do not add Inter.

## Color (copy exactly — this list is the whole palette)

Surfaces: `#0b0b0d` page · `#0a0a0c` panel · `#0f0f12` raised row · `#131317` chrome ·
`#0d0d10` table header · `#08080a` log well
Lines: `#1e1e24` panel border · `#16161b` row divider · `#2a2a31` track · `#2f2f38` button
border · `#26262d` segmented border
Red: `#e11d2b` primary · `#ff3b45` link · `#ff5c66` alert text · `#ff7a80` label ·
`#ff8a91` soft label · `#6d2b30` dim bar · `#4a181e` inset ring · `#3a2226` dashed callout ·
`rgba(225,29,43,.06–.12)` tinted fills
Blue (alliance only, never an accent): `#3a5f8f` rail · `#7fa8d8` label
Green: `#34a86a` · `#5fce93` · `#1f5138` border
Amber: `#f5a524`
Text: `#f4f4f6` primary · `#a0a0ab` · `#9a9aa4` secondary · `#8b8b95` · `#7d7d88` label ·
`#6b6b76` · `#5f5f6a` faint

Blue means "blue alliance." It is never a UI accent, link, or info color. Red is the product.

## Screens

Phone (scout, in the stands):

1. `Phone 01 - Take a Seat` — claim a station. Stations are color-coded buttons, no field map,
   deliberately, so a scout cannot mirror-flip their assignment.
2. `Phone 02 - Standby` — seat claimed, waiting for match start.
3. `Phone 03 - Offline` — queued locally, hub unreachable. Never blocks data entry.
4. `Phone 04 - Live Match` — the two-thumb HUD. Left thumb: rate ladder. Right thumb: hold
   meter. This is the screen that must feel like hardware. Touch targets are large on purpose;
   do not shrink any of them.
5. `Phone 05 - After the Buzzer` — confirm-and-send summary.

Computer (strategy laptop, 1240px):

6. `Computer 01 - Strategy Board` — live match board.
7. `Computer 02 - Match Preview` — upcoming match breakdown.
8. `Computer 03 - Team Detail` — single-team history.
9. `Computer 04 - Picklist` — draft board.
10. `Computer 05 - Data Health` — scout reliability + flagged matches.
11. `Computer 06 - Seats - Assignments` — who sits where.
12. `Computer 07 - Server Diagnostics` — Windows console for the hub machine. Windows title
    bar (glyphs right-aligned, close button on a `#2a2a31` hover fill), services table,
    sync queue, network, tailing log, connected devices with build + battery.

`Screens Index.dc.html` links all twelve.

## Domain vocabulary — non-negotiable

Terminology comes from `rules2026.json` in this folder. Use the game's words, not synonyms:
shifts, hub live, trickle, dumping, tower levels (L1/L2/L3), fuel, intervals, seats, stations
(RED 1–3 / BLUE 1–3), match codes (`Q38`). Never write "points," "score," or "robot" where the
mocks say something specific.

## Data shown in the mocks

All numbers, team numbers, scout names, device names, IPs, PIDs, and log lines are realistic
placeholders. Wire them to real data, but keep the *shape* — column counts, decimal places
(`0.96`, `6.4`), units (`14s`, `1.4 GB`, `412`), and `font-variant-numeric: tabular-nums` on
every numeric column.

## Reference screenshots — diff against these

`reference/` holds one PNG per screen, captured from the mock file at reference size:

```
reference/Phone 01 - Take a Seat.png        reference/Computer 01 - Strategy Board.png
reference/Phone 02 - Standby.png           reference/Computer 02 - Match Preview.png
reference/Phone 03 - Offline.png           reference/Computer 03 - Team Detail.png
reference/Phone 04 - Live Match.png        reference/Computer 04 - Picklist.png
reference/Phone 05 - After the Buzzer.png  reference/Computer 05 - Data Health.png
reference/Phone Platform Spec.png          reference/Computer 06 - Seats - Assignments.png
                                           reference/Computer 07 - Server Diagnostics.png
```

Use them as the visual target: render your build at reference size, screenshot it, put the two
side by side, and fix every difference you can see. They are the acceptance criteria, not a
mood board.

Two cautions. The PNGs include the presentation bezel / window chrome around the phone screens —
that frame is **not** built (see Canvas sizes). And a PNG is a rendering, not the source: when
a value is ambiguous in the image, read it out of the `.dc.html`. The markup always wins.

## Expandable per device screen — how the layout grows

Pixel-exact does **not** mean fixed-pixel. It means: at the reference size the build is
indistinguishable from the mock, and at every other size it grows by the rules below — not by
whatever your CSS framework does when you shrug.

The reference is 844 × 390 (phone) and 1240px (computer). Reproduce those exactly first. Then:

### Phone — three-column track

Every phone screen is a horizontal three-column track inside the safe box:

```
[ left column 218px ][ centre 1fr ][ right column 186px ]
```

- **Side columns are fixed.** 218px and 186px at every screen size. They hold thumb controls;
  a thumb does not get bigger on a bigger phone.
- **The centre column absorbs all extra width.** It is the only `1fr`. On a 932px iPhone 16 Pro
  Max the centre gets 88px more than reference. It does not re-centre, re-flow, or add columns —
  the existing content just breathes.
- **Vertical growth** goes to the centre column's primary block (the ladder, the summary list,
  the station grid). Header stays 44px. Bottom inset stays 20px.
- **Station buttons are a 3 × 2 grid**, RED row over BLUE row. Never one row of six. Each button
  must stay ≥ 80px wide and ≥ 88px tall at 780px, ≥ 44px always.
- **Below 780px** (iPhone SE landscape, 667px): side columns shrink by up to 10% — 218 → 196,
  186 → 168 — before anything else changes. Nothing wraps. Nothing scrolls. Nothing is hidden.
  A scout never scrolls during a match.
- **Above 932px**: cap the centre column at 640px and letterbox with the page background. Do not
  keep stretching.

Implement with flex/grid `gap`, never margins between siblings, never absolute positioning for
layout (the mock files use absolute positioning only to place the bezel — build the safe box as
a normal flex/grid container).

### Computer — 1240px is a minimum, not a maximum

- **1240px is the reference and the floor.** Below it, the window scrolls horizontally. Do not
  add a tablet or mobile layout for these screens; they run on a strategy laptop.
- **Fixed rails stay fixed.** Server Diagnostics' device rail is 360px; the dashboard rails keep
  their mock widths at every window size.
- **The main column takes all extra width.** Tables stretch their first (name/label) column and
  keep every numeric column at its mock width, right-aligned, `tabular-nums`.
- **Above 1600px**, cap the whole shell at 1600px and centre it. Do not add a fourth column,
  a new panel, or a widget to fill space.
- Panel padding, border widths, row heights, and font sizes **never** change with window size.
  Only column widths flex.

### What "expandable" never means

- Never add a breakpoint that reorders, stacks, or collapses anything mid-match.
- Never introduce a hamburger, drawer, accordion, or overflow menu that isn't in a mock.
- Never scale type with viewport units. Type sizes are literal and fixed.
- Never let a hit target shrink below 44px at any size.
- If a size genuinely breaks the layout, **stop and ask** — don't invent a breakpoint.

### Definition of done, per screen

1. At reference size, a screenshot diff against the mock file shows no visible difference.
2. At the smallest and largest tested size, nothing wraps, clips, scrolls, or drops below 44px.
3. Every value in your code traces to a value in a mock file.

## When you disagree

You will find things you would have done differently. Some of them you are right about. The
process is: implement it as specified, then list your suggestions at the end. A build that
matches and comes with three good suggestions is worth far more than a build that improved
things unasked and now matches nothing.
