# fantasy-laliga-assistant

**A decision-support tool for LaLiga Fantasy: value your squad, spot bargains, and — soon — get transfer, lineup and captain recommendations backed by a proper backtest.**

[![CI](https://github.com/dr-andromeda/fantasy-laliga-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/dr-andromeda/fantasy-laliga-assistant/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)

---

## The idea

Every gameweek a Fantasy manager makes the same decision: **who to sign, who to
sell, who to start, who to captain**, under a budget and squad rules. It's a
constrained optimization problem sitting on top of a forecasting problem — and
most people do it by gut.

This tool does it explicitly:

1. **Predict** each player's expected points for the next few gameweeks (form,
   fixtures, minutes risk).
2. **Optimize** the transfer / lineup / captain decision under the budget and
   position constraints — with an off-the-shelf ILP solver *and* with the
   metaheuristic solvers from my other projects
   ([qubo-forge](https://github.com/dr-andromeda/qubo-forge),
   [metaheuristics-jvm](https://github.com/dr-andromeda/metaheuristics-jvm)), and
   check they agree.
3. **Explain** the recommendation — the *why*, not a black-box answer.
4. **Backtest** the whole thing over past gameweeks against "do nothing" and
   naive baselines, and report the points delta honestly.

It's built around a `FantasyProvider` interface, so LaLiga Fantasy is just the
first platform — Biwenger and Comunio slot in behind the same seam.

## Status

**Pre-alpha.** What works today:

- `fla players list` — browse the player universe (price, season points, recent
  form, points per €M), filtered and sorted.
- `fla squad show` — take a hand-written `squad.yaml`, resolve the names, print it
  valued and checked against the squad rules, and **project its points** for the
  next N gameweeks (form × availability × fixture difficulty), with a band and a
  captain readout.
- `fla predict "<player>"` — the same projection for one player, broken down
  gameweek by gameweek with the reasoning.
- `fla squad lineup` — recommend the best legal XI and captain from the players
  you own, and how many points it gains over your current XI. Exact (every valid
  formation is tried).

Next: the transfer optimizer (buy/sell), then the backtest. See
[Roadmap](#roadmap).

## Install

```bash
git clone https://github.com/dr-andromeda/fantasy-laliga-assistant
cd fantasy-laliga-assistant
pip install -e ".[dev]"
```

## Use

```bash
# top forwards by recent form
fla players list --position FWD --sort form -n 10

# value a squad you typed by hand
fla squad show --squad examples/squad.example.yaml
```

### Where the data comes from

- `--source auto` (default): the LaLiga Fantasy public endpoint; if it's
  unreachable, a local `data/players.csv` you provided; failing that, the
  bundled sample.
- `--source csv`: always the bundled **fictional** sample in `data/sample/` —
  invented clubs, players and prices (regenerate with `data/sample/generate.py`).
  Deterministic, offline, used by the tests and examples. Not real data.
- `--source api`: the live endpoint only, no fallback.

To run on real LaLiga data, pull it into the (git-ignored) `data/` folder:

```bash
python scripts/fetch_squads.py                  # auto: official API, then Biwenger
python scripts/fetch_squads.py --source biwenger
```

`--source biwenger` uses [Biwenger](https://biwenger.as.com)'s public API: one
unauthenticated request gives every player's **LaLiga Fantasy price**, season
points, recent form and injury status, plus the next matchday. It's the reliable
option while the official endpoint is down. (`--source transfermarkt`, needing
`pip install -e ".[scrape]"`, gets real names only — prices/points estimated.)

`fla` then picks up `data/players.csv` automatically under `--source auto`.

### `squad.yaml`

Names are fuzzy-matched against the player universe, so accents and short forms
are fine. Add `(bench)` to keep a player out of the XI, `captain: true` to pick
your captain, and a `team:` hint to disambiguate.

```yaml
budget_remaining: 2_600_000
players:
  - Courtois
  - name: Unai Simon (bench)
  - Carvajal
  - name: Bellingham
    captain: true
  - name: Isco
    team: Betis
  - Lewandowski
  # ...
```

## How it's built

```
providers/          one adapter per platform (LaLiga Fantasy today)
  base.py           the FantasyProvider interface
  laliga_fantasy.py public API + CSV fallback
model.py            Player, Squad, Fixture, ScoringRules, Constraints  (the shared vocabulary)
squad_io.py         load squad.yaml + fuzzy-match names
valuation.py        the no-model baseline valuation
cli.py              the `fla` command
config/             per-provider scoring rules and squad constraints (YAML)
```

Everything downstream works on the `model.py` types, never on a provider's raw
payload — that's what keeps it multi-platform.

## Roadmap

- [x] Points predictor: form + availability + fixture difficulty → expected points over N gameweeks, with an uncertainty band and per-gameweek explainability (`prediction.py`)
- [ ] Real minutes model (rotation / injury history) to replace the status multiplier
- [ ] Event-level scoring engine driven by `config/*_scoring.yaml` (predict goals / assists / clean sheets, then score them)
- [x] Lineup + captain optimizer: pick the best legal XI from the players you own, exact by formation enumeration (`optimize.py`, `fla squad lineup`)
- [ ] Transfer optimizer (buy/sell) — ILP + qubo-forge / metaheuristics-jvm, compared; budget models LaLiga Fantasy's temporary-overdraft rule, not a hard cap
- [ ] **Backtest harness**: recommendations vs. hindsight-optimal vs. do-nothing, points delta per gameweek
- [ ] `import_squad()` for LaLiga Fantasy (optional, behind the same interface)
- [ ] Biwenger provider
- [ ] Web dashboard (the core is already a library)
- [ ] Scheduled weekly report (Telegram / email)

## Caveats

- The LaLiga Fantasy API is **unofficial**; this project only reads public data
  and does not redistribute it. Be gentle with it.
- The scoring rules in `config/` are **approximate** — verify against the current
  season before the scoring engine goes live.
- Football is noisy. The goal is to beat "do nothing" and naive heuristics over a
  season, not to guarantee anything.

## License

[MIT](LICENSE) © 2026 Baltasar Hurtado Jiménez
