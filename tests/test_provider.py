from __future__ import annotations

from fantasy_assistant.model import Position
from fantasy_assistant.providers import AVAILABLE, get_provider
from fantasy_assistant.providers.laliga_fantasy import LaLigaFantasyProvider


def test_registry_exposes_laliga() -> None:
    assert "laliga" in AVAILABLE
    assert isinstance(get_provider("laliga", source="csv"), LaLigaFantasyProvider)


def test_csv_universe_parses(universe: list) -> None:  # type: ignore[type-arg]
    assert len(universe) >= 30
    by_pos = {pos: [p for p in universe if p.position is pos] for pos in Position}
    assert len(by_pos[Position.GK]) >= 2
    assert len(by_pos[Position.FWD]) >= 3
    courtois = next(p for p in universe if p.name == "Courtois")
    assert courtois.team == "Real Madrid"
    assert courtois.price == 18_000_000
    assert courtois.points_by_gameweek[-1] == 10


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
    clasico = next(f for f in fx if {f.home_team, f.away_team} == {"Real Madrid", "Barcelona"})
    assert clasico.opponent_of("Real Madrid") == "Barcelona"
    assert clasico.is_home("Real Madrid")


def test_import_squad_not_supported_yet() -> None:
    prov = LaLigaFantasyProvider(source="csv")
    try:
        prov.import_squad(team_id="123")
    except NotImplementedError as e:
        assert "squad.yaml" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected NotImplementedError")
