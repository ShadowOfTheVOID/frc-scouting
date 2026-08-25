# Third-party content

## Bundled code

| What | Where | License |
|---|---|---|
| [jsQR](https://github.com/cozmo/jsQR) 1.4.0 | `web/js/vendor/jsQR.js` | MIT |
| [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4 | `web/js/vendor/qrcode.js` | MIT |
| [Barlow](https://fonts.google.com/specimen/Barlow), [Barlow Condensed](https://fonts.google.com/specimen/Barlow+Condensed), [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | `web/fonts/` | SIL Open Font License 1.1 |

Fonts are self-hosted rather than loaded from Google Fonts so typography is
identical at a venue with no internet. Only the latin subset is included.

## Bundled data

`server/fixtures/nexus_pitmap_example.json` is the example response published in the
[Nexus API docs](https://frc.nexus/api/v1/docs) for `GET /event/{eventKey}/map`. It is used
only by `seed_demo.py` to generate a realistic offline demo event, and is replaced by live
data as soon as a Nexus API key is configured.

## Data sources

This project reads from, and is grateful to:

- **[Nexus for FRC](https://frc.nexus)** — live queueing, match timing, pit map, alliance
  selection. Nexus asks that projects using its data link back to frc.nexus; both the
  scouting and dashboard surfaces do so in their footer.
- **[The Blue Alliance](https://www.thebluealliance.com)** — official match results, per-window
  fuel counts, per-robot climb.
- **[Statbotics](https://statbotics.io)** — EPA, shown alongside our own numbers.
- **[FIRST](https://www.firstinspires.org)** — the FRC Events API.

*FIRST®* and *FIRST® Robotics Competition* are registered trademarks of FIRST, which is not
affiliated with and does not endorse this project.
