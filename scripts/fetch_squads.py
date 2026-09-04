"""Pull real LaLiga squads into a local, git-ignored ``data/players.csv``.

The committed sample dataset stays fictional (see ``data/sample/``). This script
is for *your* machine: it writes ``data/players.csv``, which the provider prefers
over the sample, so ``fla`` runs on real names.

Two sources:

* ``--source api`` (default) -- the LaLiga Fantasy public endpoint. Real players,
  real prices, real points history. Everything ``fla`` needs. Sometimes down.
* ``--source transfermarkt`` -- scrapes the 20 club squad pages. Real names,
  clubs and positions; **prices are synthetic** (derived from market value) and
  there is **no points history**, so ``fla predict`` / projections are weak
  until you re-run with ``--source api``.

Usage::

    pip install -e ".[scrape]"          # for the transfermarkt source
    python scripts/fetch_squads.py                    # tries the API
    python scripts/fetch_squads.py --source transfermarkt

Be considerate with Transfermarkt: this makes ~21 requests with a pause between
them, and the data is not redistributed (``data/`` is git-ignored).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parents[1] / "data"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
FIELDS = ["id", "name", "team", "position", "price", "total_points", "points_by_gameweek", "status"]

_TM_POSITION = {
    "Goalkeeper": "GK",
    "Centre-Back": "DEF", "Left-Back": "DEF", "Right-Back": "DEF", "Defender": "DEF",
    "Defensive Midfield": "MID", "Central Midfield": "MID", "Attacking Midfield": "MID",
    "Left Midfield": "MID", "Right Midfield": "MID", "Midfielder": "MID",
    "Left Winger": "FWD", "Right Winger": "FWD", "Second Striker": "FWD",
    "Centre-Forward": "FWD", "Forward": "FWD", "Attacker": "FWD",
}


def _write(rows: list[dict[str, object]], name: str, note: str) -> None:
    DATA.mkdir(exist_ok=True)
    path = DATA / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# {note}\n")
        w = csv.DictWriter(fh, fieldnames=FIELDS if name == "players.csv" else list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")


# --- source: LaLiga Fantasy API -----------------------------------------------

_API = "https://api-fantasy.llt-services.com/api"
_POS_BY_ID = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def from_api() -> None:
    with httpx.Client(headers={"User-Agent": UA}, timeout=20.0) as client:
        resp = client.get(f"{_API}/v3/players")
        resp.raise_for_status()
        raw = resp.json()

    players: list[dict[str, object]] = []
    for p in raw:
        team = (p.get("team") or {}).get("name") or p.get("teamName") or "unknown"
        weeks = p.get("weeksPoints") or []
        history = [int(w.get("points", 0)) if isinstance(w, dict) else int(w) for w in weeks]
        players.append({
            "id": str(p["id"]),
            "name": p.get("nickname") or p.get("name") or f"player-{p['id']}",
            "team": team,
            "position": _POS_BY_ID.get(int(p.get("positionId", 0)), "MID"),
            "price": int(float(p.get("marketValue") or 0)),
            "total_points": int(p.get("points") or 0),
            "points_by_gameweek": ";".join(str(x) for x in history),
            "status": str(p.get("playerStatus") or "ok").lower(),
        })
    _write(players, "players.csv", "Real LaLiga Fantasy data (API). Local copy, not committed.")


# --- source: Transfermarkt ---------------------------------------------------

_MV = re.compile(r"([\d.]+)\s*(bn|m|k)?", re.IGNORECASE)


def _market_value_eur(text: str) -> int:
    """'€45.00m' -> 45_000_000 ;  '€800k' -> 800_000 ;  '-' -> 0."""
    m = _MV.search(text.replace("€", "").strip())
    if not m:
        return 0
    scale = {"bn": 1_000_000_000, "m": 1_000_000, "k": 1_000}.get((m.group(2) or "").lower(), 1)
    return int(float(m.group(1)) * scale)


def _synthetic_from_value(market_value_eur: int) -> tuple[int, int, str]:
    """Market value -> (fantasy price, total_points, points_by_gameweek).

    All three are rough monotonic estimates so the tool can rank players; they
    are NOT real. Re-run with --source api for genuine prices and form.
    """
    millions = market_value_eur / 1_000_000
    price = int(max(1_000_000, min(45_000_000, round(1_000_000 + millions**0.62 * 2_200_000))))
    per_game = max(1, min(13, round(millions**0.42)))
    history = ";".join(str(per_game) for _ in range(5))
    return price, per_game * 5, history


def from_transfermarkt() -> None:
    try:
        from bs4 import BeautifulSoup
    except ModuleNotFoundError:
        sys.exit("needs BeautifulSoup: pip install -e \".[scrape]\"")

    base = "https://www.transfermarkt.com"
    with httpx.Client(headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True) as client:
        overview = client.get(f"{base}/laliga/startseite/wettbewerb/ES1")
        overview.raise_for_status()
        soup = BeautifulSoup(overview.text, "html.parser")

        clubs: dict[str, tuple[str, str]] = {}
        table = soup.select("table.items")[0]
        for a in table.select("a[href*='/kader/verein/']"):
            m = re.search(r"(/[a-z0-9-]+/kader/verein/(\d+)/saison_id/\d+)", a["href"])
            if m:
                clubs[m.group(2)] = (a.get("title") or m.group(1), m.group(1))
        print(f"{len(clubs)} clubs")

        players: list[dict[str, object]] = []
        pid = 0
        for club_name, kader_path in clubs.values():
            time.sleep(1.5)
            page = client.get(f"{base}{kader_path}/plus/1")
            if page.status_code != 200:
                print(f"  ! {club_name}: HTTP {page.status_code}, skipped")
                continue
            rows = BeautifulSoup(page.text, "html.parser").select("table.items tbody > tr")
            count = 0
            for tr in rows:
                name_el = tr.select_one("td.hauptlink a")
                if not name_el:
                    continue
                inline = tr.select("td.posrela table.inline-table tr")
                pos_text = inline[1].get_text(strip=True) if len(inline) > 1 else ""
                mv_el = tr.select_one("td.rechts.hauptlink")
                mv = _market_value_eur(mv_el.get_text(strip=True) if mv_el else "")
                price, total, history = _synthetic_from_value(mv)
                pid += 1
                players.append({
                    "id": f"tm{pid:04d}",
                    "name": name_el.get_text(strip=True),
                    "team": club_name,
                    "position": _TM_POSITION.get(pos_text, "MID"),
                    "price": price,
                    "total_points": total,
                    "points_by_gameweek": history,
                    "status": "ok",
                })
                count += 1
            print(f"  {club_name}: {count}")

    _write(
        players, "players.csv",
        "Real names/clubs/positions from Transfermarkt; prices AND points are rough "
        "estimates from market value, not real. Re-run with --source api for genuine "
        "data. Local copy, not committed.",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=("api", "transfermarkt"), default="api")
    args = ap.parse_args()
    if args.source == "api":
        from_api()
    else:
        from_transfermarkt()


if __name__ == "__main__":
    main()
