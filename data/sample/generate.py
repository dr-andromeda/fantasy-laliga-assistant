"""Regenerate the fictional sample dataset.

The data in `players.csv` / `fixtures.csv` is INVENTED — a made-up league with
made-up clubs, players, prices and points. Its only job is to let `fla` run and
the tests pass with no network. Real data comes from the LaLiga Fantasy API
(`fla ... --source api`). Any resemblance to real footballers is coincidental.

Run:  python data/sample/generate.py
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

HERE = Path(__file__).parent

CLUBS = [
    "Costa Verde CF", "Almaden CD", "Rio Sella SD", "Penarroya FC", "Valdes UD",
    "Montoro CF", "Sierra Azul CD", "Puerto Lobo FC", "Najar Balompie", "Coto Real CF",
]

# (name, club index, position, quality 1-10). Quality drives price and points.
ROSTER: list[tuple[str, int, str, int]] = [
    # goalkeepers
    ("Bermudez", 0, "GK", 6), ("Catalan", 2, "GK", 7), ("Ferreras", 4, "GK", 5),
    ("Quiroga", 6, "GK", 6), ("Sedano", 8, "GK", 4), ("Ubach", 1, "GK", 5),
    # defenders
    ("Benavente", 0, "DEF", 7), ("Cardus", 0, "DEF", 5), ("Elorza", 1, "DEF", 6),
    ("Gamez", 1, "DEF", 5), ("Hidalgo", 2, "DEF", 8), ("Iniguez", 2, "DEF", 6),
    ("Lozano", 3, "DEF", 5), ("Miralles", 3, "DEF", 6), ("Prats", 4, "DEF", 5),
    ("Ruano", 5, "DEF", 7), ("Vilanova", 6, "DEF", 6), ("Bejarano", 7, "DEF", 5),
    ("Castells", 8, "DEF", 4), ("Donaire", 9, "DEF", 6),
    # midfielders
    ("Abad", 0, "MID", 9), ("Canete", 0, "MID", 6), ("Dorado", 1, "MID", 8),
    ("Escamez", 1, "MID", 6), ("Fandino", 2, "MID", 9), ("Gascon", 2, "MID", 5),
    ("Herran", 3, "MID", 7), ("Jimeno", 3, "MID", 6), ("Lastra", 4, "MID", 7),
    ("Maroto", 5, "MID", 6), ("Novoa", 5, "MID", 8), ("Peralta", 6, "MID", 6),
    ("Rendon", 7, "MID", 7), ("Solis", 9, "MID", 6),
    # forwards
    ("Amador", 0, "FWD", 9), ("Bustos", 1, "FWD", 8), ("Cifre", 2, "FWD", 9),
    ("Devesa", 3, "FWD", 6), ("Espasa", 4, "FWD", 7), ("Fabra", 5, "FWD", 8),
    ("Huertas", 6, "FWD", 6), ("Ibarrola", 7, "FWD", 7), ("Jativa", 8, "FWD", 5),
    ("Loredo", 9, "FWD", 6),
]


def build_players(seed: int = 2026) -> list[dict[str, object]]:
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []
    for i, (name, club_idx, pos, quality) in enumerate(ROSTER, start=1):
        base = quality + rng.uniform(-0.6, 0.6)                 # per-game points level
        history = [max(0, round(rng.gauss(base, 2.0))) for _ in range(7)]
        total = sum(history)
        price = round((quality * 3.1 + rng.uniform(-1.2, 1.2)) * 1_000_000)
        status = "ok"
        roll = rng.random()
        if roll < 0.06:
            status = "injured"
        elif roll < 0.15:
            status = "doubtful"
        rows.append({
            "id": f"p{i:02d}",
            "name": name,
            "team": CLUBS[club_idx],
            "position": pos,
            "price": max(1_000_000, price),
            "total_points": total,
            "points_by_gameweek": ";".join(str(x) for x in history),
            "status": status,
        })
    return rows


def build_fixtures() -> list[dict[str, object]]:
    # a simple rotating schedule for gameweeks 8, 9, 10
    n = len(CLUBS)
    rows: list[dict[str, object]] = []
    for gw_offset, shift in enumerate((0, 3, 6)):
        gw = 8 + gw_offset
        used: set[int] = set()
        for a in range(n):
            b = (a + 1 + shift) % n
            if a in used or b in used or a == b:
                continue
            used.add(a)
            used.add(b)
            rows.append({"gameweek": gw, "home_team": CLUBS[a], "away_team": CLUBS[b]})
    return rows


def main() -> None:
    players = build_players()
    with (HERE / "players.csv").open("w", newline="", encoding="utf-8") as fh:
        fh.write("# FICTIONAL sample data (invented league). Not real. See generate.py.\n")
        w = csv.DictWriter(fh, fieldnames=list(players[0]))
        w.writeheader()
        w.writerows(players)

    fixtures = build_fixtures()
    with (HERE / "fixtures.csv").open("w", newline="", encoding="utf-8") as fh:
        fh.write("# FICTIONAL sample fixtures. Not real.\n")
        w = csv.DictWriter(fh, fieldnames=list(fixtures[0]))
        w.writeheader()
        w.writerows(fixtures)

    print(f"wrote {len(players)} players, {len(fixtures)} fixtures")


if __name__ == "__main__":
    main()
