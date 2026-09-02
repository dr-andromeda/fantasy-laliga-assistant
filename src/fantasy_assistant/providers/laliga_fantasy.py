"""LaLiga Fantasy (the official Marca game) provider.

Public player and fixture data is read from the community-known JSON API. That
API is unofficial and occasionally unavailable, so every fetch falls back to a
local CSV snapshot; a small sample snapshot ships in ``data/sample/`` so the tool
(and its tests) work with no network at all.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml

from fantasy_assistant.model import (
    Constraints,
    Fixture,
    Player,
    PlayerStatus,
    Position,
    ScoringRules,
)
from fantasy_assistant.providers.base import FantasyProvider

_API_BASE = "https://api-fantasy.llt-services.com/api"
_POSITION_BY_ID = {1: Position.GK, 2: Position.DEF, 3: Position.MID, 4: Position.FWD}
_STATUS_MAP = {
    "ok": PlayerStatus.OK,
    "doubt": PlayerStatus.DOUBTFUL,
    "doubtful": PlayerStatus.DOUBTFUL,
    "injured": PlayerStatus.INJURED,
    "sanctioned": PlayerStatus.SUSPENDED,
    "suspended": PlayerStatus.SUSPENDED,
}

Source = Literal["auto", "api", "csv"]


class LaLigaFantasyProvider(FantasyProvider):
    key = "laliga"

    def __init__(
        self,
        *,
        source: Source = "auto",
        data_dir: Path | str = "data",
        timeout: float = 15.0,
    ) -> None:
        self.source = source
        self.data_dir = Path(data_dir)
        self.timeout = timeout

    # -- public API ------------------------------------------------------
    def load_players(self) -> list[Player]:
        if self.source in ("auto", "api"):
            try:
                raw = self._get("/v3/players")
                self._snapshot("players", raw)
                return [self._parse_player(p) for p in raw]
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                if self.source == "api":
                    raise
                print(f"[laliga] live API unavailable ({exc}); using CSV snapshot")
        return self._players_from_csv(self._resolve_csv("players.csv"))

    def fixtures(self, upcoming: int = 5) -> list[Fixture]:
        path = self._resolve_csv("fixtures.csv")
        if not path.exists():
            return []
        out: list[Fixture] = []
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                out.append(
                    Fixture(
                        gameweek=int(row["gameweek"]),
                        home_team=row["home_team"].strip(),
                        away_team=row["away_team"].strip(),
                    )
                )
        weeks = sorted({f.gameweek for f in out})[:upcoming]
        return [f for f in out if f.gameweek in weeks]

    def scoring_rules(self) -> ScoringRules:
        data = self._load_config("laliga_scoring.yaml")
        return ScoringRules(provider=self.key, events=data.get("events", {}))

    def constraints(self) -> Constraints:
        data = self._load_config("laliga_constraints.yaml")
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
    def _get(self, path: str) -> Any:
        headers = {"User-Agent": "fantasy-laliga-assistant/0.1 (+github.com/dr-andromeda)"}
        resp = httpx.get(f"{_API_BASE}{path}", headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _snapshot(self, name: str, payload: Any) -> None:
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = self.data_dir / "snapshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}_{stamp}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    # -- parsing ----------------------------------------------------
    @staticmethod
    def _parse_player(raw: dict[str, Any]) -> Player:
        team = raw.get("team") or {}
        weeks = raw.get("weeksPoints") or raw.get("points_by_week") or []
        history = [int(w.get("points", 0)) if isinstance(w, dict) else int(w) for w in weeks]
        status_raw = str(raw.get("playerStatus") or raw.get("status") or "").lower()
        return Player(
            id=str(raw["id"]),
            name=raw.get("nickname") or raw.get("name") or f"player-{raw['id']}",
            team=team.get("name") or raw.get("teamName") or "unknown",
            position=_POSITION_BY_ID.get(int(raw.get("positionId", 0)), Position.MID),
            price=int(float(raw.get("marketValue") or raw.get("price") or 0)),
            total_points=int(raw.get("points") or 0),
            points_by_gameweek=history,
            status=_STATUS_MAP.get(status_raw, PlayerStatus.UNKNOWN),
            provider="laliga",
        )

    @staticmethod
    def _players_from_csv(path: Path) -> list[Player]:
        players: list[Player] = []
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                history_raw = (row.get("points_by_gameweek") or "").strip()
                history = [int(x) for x in history_raw.split(";") if x] if history_raw else []
                players.append(
                    Player(
                        id=row["id"].strip(),
                        name=row["name"].strip(),
                        team=row["team"].strip(),
                        position=Position(row["position"].strip().upper()),
                        price=int(row["price"]),
                        total_points=int(row.get("total_points") or 0),
                        points_by_gameweek=history,
                        status=PlayerStatus(row.get("status", "unknown").strip() or "unknown"),
                        provider="laliga",
                    )
                )
        return players

    # -- file lookup ------------------------------------------------
    def _resolve_csv(self, name: str) -> Path:
        local = self.data_dir / name
        if local.exists():
            return local
        return self._sample_path(name)

    @staticmethod
    def _sample_path(name: str) -> Path:
        # data/sample/ lives next to the repo root, not inside the package
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "data" / "sample" / name
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"no snapshot for {name!r} and no sample bundled")

    @staticmethod
    def _load_config(name: str) -> dict[str, Any]:
        text = resources.files("fantasy_assistant.config").joinpath(name).read_text("utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"config {name!r} is not a mapping")
        return data
