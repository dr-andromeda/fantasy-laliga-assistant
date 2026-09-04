from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from fantasy_assistant.model import Position
from fantasy_assistant.providers import AVAILABLE, get_provider
from fantasy_assistant.providers.laliga_fantasy import LaLigaFantasyProvider

_PLAYERS_HEADER = "id,name,team,position,price,total_points,points_by_gameweek,status\n"


def test_registry_exposes_laliga() -> None:
    assert "laliga" in AVAILABLE
    assert isinstance(get_provider("laliga", source="csv"), LaLigaFantasyProvider)


def test_csv_universe_parses(universe: list) -> None:  # type: ignore[type-arg]
    assert len(universe) >= 30
    by_pos = {pos: [p for p in universe if p.position is pos] for pos in Position}
    assert len(by_pos[Position.GK]) >= 2
    assert len(by_pos[Position.FWD]) >= 3
    catalan = next(p for p in universe if p.name == "Catalan")
    assert catalan.team == "Rio Sella SD"
    assert catalan.position is Position.GK
    assert catalan.price > 0
    assert len(catalan.points_by_gameweek) == 7


def test_scoring_rules_and_constraints_parse() -> None:
    prov = LaLigaFantasyProvider(source="csv")
    rules = prov.scoring_rules()
    assert rules.points_for("goal_FWD") == 4
    assert rules.points_for("nonexistent_event") == 0.0

    c = prov.constraints()
    assert c.squad_size == 15
    assert c.squad_by_position[Position.DEF] == 5
    assert c.lineup_max_by_position[Position.GK] == 1


def test_fixtures_load_and_limit() -> None:
    prov = LaLigaFantasyProvider(source="csv")
    fx = prov.fixtures(upcoming=2)
    assert {f.gameweek for f in fx} == {8, 9}
    opener = next(
        f for f in fx if {f.home_team, f.away_team} == {"Costa Verde CF", "Almaden CD"}
    )
    assert opener.opponent_of("Costa Verde CF") == "Almaden CD"
    assert opener.is_home("Costa Verde CF")


def test_source_csv_ignores_a_local_override(tmp_path: Path) -> None:
    (tmp_path / "players.csv").write_text(
        _PLAYERS_HEADER + "x,Fake,T,GK,1,0,,ok\n", encoding="utf-8"
    )
    prov = LaLigaFantasyProvider(source="csv", data_dir=tmp_path)
    names = {p.name for p in prov.load_players()}
    assert "Fake" not in names          # explicit csv source == the bundled sample
    assert "Catalan" in names


def test_source_auto_prefers_a_local_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "players.csv").write_text(
        _PLAYERS_HEADER + "x,LocalGuy,T,MID,1000000,0,,ok\n", encoding="utf-8"
    )
    prov = LaLigaFantasyProvider(source="auto", data_dir=tmp_path)

    def _boom(_path: str) -> object:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(prov, "_get", _boom)
    assert {p.name for p in prov.load_players()} == {"LocalGuy"}


def test_import_squad_not_supported_yet() -> None:
    prov = LaLigaFantasyProvider(source="csv")
    try:
        prov.import_squad(team_id="123")
    except NotImplementedError as e:
        assert "squad.yaml" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected NotImplementedError")
