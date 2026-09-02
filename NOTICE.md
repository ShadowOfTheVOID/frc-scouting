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

`server/fixtures/lovat_report_example.csv` is a small hand-written file in the shape of Lovat's
`GET /v1/analysis/reportcsv` export. It contains no real scouting data — the rows are invented
to exercise the parser — and is used only by `server/tests_lovat.py`.

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
- **[Lovat](https://lovat.app)**, by FRC 8033 Highlander Robotics — other teams' scout reports
  for the same event, read with the team's own API key and shown in a block of its own. The
  endpoints used are documented by its open-source backend,
  [HighlanderRobotics/lovat-server](https://github.com/HighlanderRobotics/lovat-server).
- **[FIRST](https://www.firstinspires.org)** — the FRC Events API.

## Optional AI providers

The generated panels are off by default. When a provider is configured, the hub calls one of
[Anthropic](https://www.anthropic.com), [OpenAI](https://openai.com) or
[Google Gemini](https://ai.google.dev) over plain HTTPS with the team's own key. No vendor SDK
is bundled or required, and nothing is sent to any of them unless somebody presses a button.
What is sent is the trimmed per-team record described in
[docs/features.md](docs/features.md#ai) — robot data, scout ids and scout notes; no other
personal data.

*FIRST®* and *FIRST® Robotics Competition* are registered trademarks of FIRST, which is not
affiliated with and does not endorse this project.
