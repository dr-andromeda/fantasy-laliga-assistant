"""Biwenger as a second, live data source for LaLiga Fantasy prices and points.

Biwenger runs its own fantasy game, but its public API's ``price`` field
mirrors the *official* LaLiga Fantasy (Marca) price -- not Biwenger's own
in-game economy, which is the separate, inflated ``fantasyPrice`` field
(verified by cross-checking known real prices: Mbappe ~24.8M, not the ~178M
``fantasyPrice`` would suggest). So this provider borrows LaLiga Fantasy's
own squad rules and scoring config (``config/laliga_*.yaml``) rather than
inventing a Biwenger-specific rule set -- it's the same game, reached
through a second, unauthenticated endpoint, useful while the official API in
``laliga_fantasy.py`` is down.

One request returns every player's price, season points, recent form and
injury status, plus each team's *next* fixture only -- so :meth:`fixtures`
can never return more than a gameweek or two, unlike the official API.

See ``scripts/fetch_squads.py`` for the standalone script this provider's
parsing logic is shared with.
"""

from __future__ import annotations

from typing import Any

import httpx

from fantasy_assistant.model import (
    Constraints,
    Fixture,
    Player,
    PlayerStatus,
    Position,
    ScoringRules,
)
from fantasy_assistant.providers.base import FantasyProvider, load_yaml_config

_API = "https://cf.biwenger.com/api/v2/competitions/la-liga/data?lang=es&score=1"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
_POSITION_BY_ID = {1: Position.GK, 2: Position.DEF, 3: Position.MID, 4: Position.FWD}
_STATUS_MAP = {
    "ok": PlayerStatus.OK,
    "doubt": PlayerStatus.DOUBTFUL,
    "sanctioned": PlayerStatus.SUSPENDED,
    "injured": PlayerStatus.INJURED,
    "discarded": PlayerStatus.INJURED,
}


class BiwengerProvider(FantasyProvider):
    key = "biwenger"

    def __init__(self, *, source: str = "auto", timeout: float = 20.0) -> None:
        if source not in ("auto", "api"):
            raise ValueError(
                f"BiwengerProvider is live-only (no offline sample) -- got source={source!r}"
            )
        self.timeout = timeout
        self._cache: dict[str, Any] | None = None

    # -- public API ------------------------------------------------------
    def load_players(self) -> list[Player]:
        data = self._data()
        team_name = {int(tid): t["name"] for tid, t in data["teams"].items()}

        players: list[Player] = []
        for p in data["players"].values():
            pos = _POSITION_BY_ID.get(p.get("position"))
            if pos is None:  # position 5 == coach, not a player
                continue
            # fitness slots are a GW score (int), None, or a status string ("injured", ...)
            fitness = [int(x) for x in (p.get("fitness") or []) if isinstance(x, (int, float))]
            players.append(
                Player(
                    id=str(p["id"]),
                    name=p["name"],
                    team=team_name.get(p.get("teamID"), "unknown"),
                    position=pos,
                    price=int(p.get("price") or 0),
                    total_points=int(p.get("points") or 0),
                    points_by_gameweek=fitness,
                    status=_STATUS_MAP.get(str(p.get("status") or ""), PlayerStatus.UNKNOWN),
                    provider=self.key,
                )
            )
        return players

    def fixtures(self, upcoming: int = 5) -> list[Fixture]:
        data = self._data()
        team_name = {int(tid): t["name"] for tid, t in data["teams"].items()}

        games: dict[int, dict[str, Any]] = {}
        for t in data["teams"].values():
            for g in t.get("nextGames") or []:
                games[g["id"]] = g

        round_date: dict[int, int] = {}
        for g in games.values():
            rid = g["round"]["id"]
            round_date[rid] = min(round_date.get(rid, g["date"]), g["date"])
        ordered_rounds = sorted(round_date, key=lambda rid: round_date[rid])
        gw_of = {rid: i + 1 for i, rid in enumerate(ordered_rounds)}

        out = [
            Fixture(
                gameweek=gw_of[g["round"]["id"]],
                home_team=team_name.get(g["home"]["id"], "?"),
                away_team=team_name.get(g["away"]["id"], "?"),
            )
            for g in sorted(games.values(), key=lambda g: g["date"])
        ]
        weeks = sorted({f.gameweek for f in out})[:upcoming]
        return [f for f in out if f.gameweek in weeks]

    def scoring_rules(self) -> ScoringRules:
        data = load_yaml_config("laliga_scoring.yaml")
        return ScoringRules(provider=self.key, events=data.get("events", {}))

    def constraints(self) -> Constraints:
        data = load_yaml_config("laliga_constraints.yaml")
        return Constraints(
            provider=self.key,
            squad_size=data["squad_size"],
            lineup_size=data["lineup_size"],
            squad_by_position={Position(k): v for k, v in data["squad_by_position"].items()},
            lineup_min_by_position={
                Position(k): v for k, v in data.get("lineup_min_by_position", {}).items()
            },
            lineup_max_by_position={
                Position(k): v for k, v in data.get("lineup_max_by_position", {}).items()
            },
            max_players_per_club=data.get("max_players_per_club"),
        )

    # -- HTTP ---------------------------------------------------------
    def _data(self) -> dict[str, Any]:
        if self._cache is None:
            with httpx.Client(headers={"User-Agent": _UA}, timeout=self.timeout) as client:
                resp = client.get(_API)
                resp.raise_for_status()
                self._cache = resp.json()["data"]
        return self._cache
