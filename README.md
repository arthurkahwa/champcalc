# Champ Calc

An iOS app that calculates, at any point in an F1 season, what each
driver/team still needs to become World Champion (or when they're
mathematically eliminated) — generalized to any finishing position and any
checkpoint race, not just the championship at season end.

This repository is Champ Calc's **GitHub-as-backend**: no server, no database.
Season data is a static JSON file, kept up to date by a scheduled GitHub
Action, and read by the app directly from this repo's raw content.

## What's here

- **`data/season-{year}.json`** — the live season data the app fetches via
  `raw.githubusercontent.com`: driver/team roster, the points table, and the
  full race-by-race calendar with results for every completed round. The
  app recomputes standings, title-contention status, and the position
  ladder client-side from this data — nothing is pre-aggregated here beyond
  what's needed for change detection.
- **`scripts/update_season.py`** — fetches current standings and per-round
  results from [Jolpica-F1](https://api.jolpi.ca/ergast/f1) (a free,
  open-source successor to the retired Ergast API), diffs against the
  committed JSON by hash, and writes an update only when something actually
  changed.
- **`scripts/f1_math.py`** — the championship-math reference implementation
  (max points remaining, elimination/clinch checks, the position ladder,
  tiebreak countback). Ported function-for-function into the app's Swift
  calc engine, so the two stay in sync by construction.
- **`.github/workflows/update-season.yml`** — runs the update script every
  15 minutes on race weekends and once daily otherwise, and commits the
  result only if it changed.

Once a season's final round is in, its data is locked in permanently
(`seasonStatus: "final"`) and the pipeline stops touching it — the file
becomes a plain historical record.

## Data source

Results and standings come from [Jolpica-F1](https://api.jolpi.ca/ergast/f1),
a free, no-auth, community-maintained successor to the Ergast API. No driver
photos or team logos are used anywhere in the app or this repo — both are
licensed IP not covered by any free data source, so the app uses initials
avatars and team colors instead.

## Privacy & support

Champ Calc collects no user data beyond an opt-in push notification device
token. See the [Privacy Policy](https://arthurkahwa.github.io/champcalc/privacy.html)
and [Support](https://arthurkahwa.github.io/champcalc/support.html) pages,
published via GitHub Pages from the root of `master`.

## License

MIT — see [LICENSE](LICENSE).
