// The three shooting speeds, in the words a scout sees.
//
// The ids are what is stored and what the solver reads; only the labels are
// for people. Listed fastest first, the way the two-thumb ladder reads. The
// match screen (hud.js) and the practice tutorial both read this, so the
// two can never call the same speed different things.
export const SPEEDS = [
  { id: 'dumping', label: 'Lots' },
  { id: 'steady',  label: 'Steady' },
  { id: 'trickle', label: 'A few' },
];
