# fantasy-laliga-assistant

**A decision-support tool for LaLiga Fantasy: value your squad, spot bargains, and get transfer, lineup and captain recommendations backed by a proper backtest.**

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

It's built around a `FantasyProvider` interface — LaLiga Fantasy's own API and
Biwenger's already slot in behind the same seam; Comunio would be a third.

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
- `fla squad transfers` — suggest same-position swaps that raise your projected
  points, under your real budget. Exact branch-and-bound over a pruned candidate
  set; see [How the transfer optimizer works](#how-the-transfer-optimizer-works).
- `fla squad backtest` — walk-forward backtest of the lineup/captain pick
  against a static "do nothing" XI and a hindsight-optimal upper bound, using
  each player's own trailing gameweek history. No lookahead: every prediction
  only sees gameweeks before the one it's scored against. See
  [How the backtest works](#how-the-backtest-works).

See [Roadmap](#roadmap) for what's still open.

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

# suggest up to 3 transfers that raise your projected points
fla squad transfers --squad examples/squad.example.yaml --transfers 3

# backtest the lineup/captain pick against static and hindsight
fla squad backtest --squad examples/squad.example.yaml
```

### Where the data comes from

Two ways to get real players instead of the fictional sample:

**1. `--provider biwenger`** — a second, live `FantasyProvider` backed by
[Biwenger](https://biwenger.as.com)'s public, unauthenticated API. No setup,
no local file — every command works straight away:

```bash
fla players list --provider biwenger --sort form -n 10
fla squad show --provider biwenger --squad my_real_squad.yaml
```

Biwenger runs its own game, but its `price` field mirrors the *official*
LaLiga Fantasy price (verified against known real prices), so this provider
reuses LaLiga Fantasy's own squad rules and scoring config — see
`providers/biwenger.py`. It only ever has the *next* gameweek of fixtures.

**2. `--provider laliga` sources** — the original provider, with a fallback chain:

- `--source auto` (default): the official LaLiga Fantasy endpoint; if
  unreachable, a local `data/players.csv` you provided; failing that, the
  bundled sample.
- `--source csv`: always the bundled **fictional** sample in `data/sample/` —
  invented clubs, players and prices (regenerate with `data/sample/generate.py`).
  Deterministic, offline, used by the tests and examples. Not real data.
- `--source api`: the live endpoint only, no fallback.

To populate that local `data/players.csv` (git-ignored) — useful for working
offline, or when you want `laliga`'s multi-gameweek fixtures rather than
Biwenger's single one:

```bash
python scripts/fetch_squads.py                  # auto: official API, then Biwenger
python scripts/fetch_squads.py --source biwenger
```

(`--source transfermarkt`, needing `pip install -e ".[scrape]"`, gets real
names only — prices/points are estimated, not real.)

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

## How the transfer optimizer works

`fla squad transfers` only proposes **same-position swaps** — the one kind of
transfer that keeps a LaLiga Fantasy squad (2 GK / 5 DEF / 5 MID / 3 FWD)
legal on its own, no second move required to rebalance. For each owned
player it keeps the top few same-position replacements by projected gain
(`--candidates`, default 5), then an exact branch-and-bound search picks the
combination — up to `--transfers` moves, never buying the same target twice —
with the highest total gain that fits the budget and any per-club cap.

**The budget rule.** LaLiga Fantasy lets a signing push your balance negative
with no cap on how far — but if you're still in the red when the *next*
gameweek kicks off, **you score zero points that gameweek**, whatever your
lineup ([official rules](https://fantasy-marca.helpscoutdocs.com/article/381-reglas-del-juego),
[confirmed here](https://laligafantasy.zendesk.com/hc/en-us/articles/360007533594) —
verified September 2026). That risk dwarfs almost any transfer gain, so by
default the optimizer treats the budget as a hard cap. Pass
`--allow-overdraft` only if you intend to clear the gap yourself before the
deadline (e.g. by rescinding a contract at 80% value) — the plan then tells
you exactly how far short you are.

## How the backtest works

`fla squad backtest` replays each owned player's own trailing gameweek
history week by week. At step `g` it predicts using only games *before* `g`
(an expanding window, recency-weighted the same way as the live predictor)
and scores the pick against what actually happened in game `g` — no
lookahead. Three arms are compared:

- **Recommended** — the lineup/captain the form-based forecast would have
  picked, scored on the real outcome.
- **Static** — whatever XI and captain `squad.yaml` already flags, replayed
  unchanged every week. The "do nothing" baseline.
- **Hindsight** — the best possible XI and captain *for that gameweek*,
  chosen with perfect knowledge of the actual points. An upper bound, not a
  strategy anyone could have played.

Recommended and hindsight are the *same* call to
`optimize.lineup_from_expected` — only the points mapping changes (forecast
vs. actual). Whatever gap remains between them is exactly the cost of not
knowing the future in advance, not a difference in how the two picks were
made.

**Scope, honestly.** This only backtests the lineup/captain choice, using
form alone — there's no historical fixture-difficulty or per-week injury
status to draw on (only the current squad snapshot has that), and it doesn't
touch transfers, which would need a season of price history this project
doesn't have. Treat the output as "how good is the lineup-picking logic on
its own," not a claim about the full tool.

## How it's built

```
providers/          one adapter per platform
  base.py           the FantasyProvider interface + shared YAML config loader
  laliga_fantasy.py official public API + CSV fallback + fictional sample
  biwenger.py       Biwenger's public API, live, no local file needed
model.py            Player, Squad, Fixture, ScoringRules, Constraints  (the shared vocabulary)
squad_io.py         load squad.yaml + fuzzy-match names
valuation.py        the no-model baseline valuation
prediction.py       the points predictor (form x minutes x fixture)
optimize.py         lineup + captain optimizer
transfers.py        transfer optimizer (same-position swaps, budget-aware)
backtest.py         walk-forward backtest: recommended vs. static vs. hindsight
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
- [x] Transfer optimizer: same-position swaps, exact branch-and-bound over a pruned candidate set, budget-aware per the verified overdraft rule (`transfers.py`, `fla squad transfers`)
- [ ] Cross-position squad restructuring, and a solver comparison against qubo-forge / metaheuristics-jvm on the larger combinatorial version of the problem
- [x] **Backtest harness**: lineup/captain recommendation vs. hindsight-optimal vs. do-nothing, walk-forward with no lookahead (`backtest.py`, `fla squad backtest`)
- [ ] Extend the backtest to cover transfers (needs a season of historical prices, which isn't available yet) and real fixture/minutes history instead of form alone
- [ ] `import_squad()` for LaLiga Fantasy (optional, behind the same interface)
- [x] Biwenger provider: live, unauthenticated, no local file needed, reuses LaLiga Fantasy's own rules (`providers/biwenger.py`, `--provider biwenger`)
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
