from __future__ import annotations

import pytest

from fantasy_assistant.model import Position
from fantasy_assistant.providers import AVAILABLE, get_provider
from fantasy_assistant.providers.biwenger import BiwengerProvider

_PAYLOAD = {
    "teams": {
        "1": {
            "name": "Home FC",
            "nextGames": [
                {"id": 100, "date": 1000, "round": {"id": 5}, "home": {"id": 1}, "away": {"id": 2}}
            ],
        },
        "2": {
            "name": "Away FC",
            "nextGames": [
                {"id": 100, "date": 1000, "round": {"id": 5}, "home": {"id": 1}, "away": {"id": 2}}
            ],
        },
    },
    "players": {
        "10": {
            "id": 10, "name": "Star", "teamID": 1, "position": 3,
            "price": 15_000_000, "points": 40,
            "fitness": [8, "injured", 6, None, 5],
            "status": "ok",
        },
        "11": {
            "id": 11, "name": "Coach Guy", "teamID": 2, "position": 5,
            "price": 0, "points": 0, "fitness": [], "status": "ok",
        },
        "12": {
            "id": 12, "name": "Hurt", "teamID": 2, "position": 4,
            "price": 3_000_000, "points": 5, "fitness": [], "status": "injured",
        },
    },
}


@pytest.fixture
def provider() -> BiwengerProvider:
    prov = BiwengerProvider()
    prov._cache = _PAYLOAD  # avoid hitting the network in tests
    return prov


def test_registry_exposes_biwenger() -> None:
    assert "biwenger" in AVAILABLE
    assert isinstance(get_provider("biwenger"), BiwengerProvider)


def test_source_csv_is_rejected() -> None:
    with pytest.raises(ValueError, match="live-only"):
        BiwengerProvider(source="csv")


def test_load_players_parses_and_skips_the_coach(provider: BiwengerProvider) -> None:
    players = {p.id: p for p in provider.load_players()}

    assert "11" not in players  # position 5 == coach, excluded
    star = players["10"]
    assert star.name == "Star"
    assert star.team == "Home FC"
    assert star.position is Position.MID
    assert star.price == 15_000_000
    assert star.total_points == 40
    assert star.points_by_gameweek == [8, 6, 5]  # "injured" and None dropped
    assert star.provider == "biwenger"

    hurt = players["12"]
    assert hurt.status.value == "injured"


def test_fixtures_numbers_the_single_round_gw1(provider: BiwengerProvider) -> None:
    fx = provider.fixtures(upcoming=5)
    assert len(fx) == 1
    assert fx[0].gameweek == 1
    assert {fx[0].home_team, fx[0].away_team} == {"Home FC", "Away FC"}


def test_scoring_rules_and_constraints_reuse_laliga_fantasy(provider: BiwengerProvider) -> None:
    rules = provider.scoring_rules()
    assert rules.provider == "biwenger"
    assert rules.points_for("goal_FWD") == 4

    c = provider.constraints()
    assert c.provider == "biwenger"
    assert c.squad_size == 15
    assert c.squad_by_position[Position.DEF] == 5


def test_caches_the_api_response_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = BiwengerProvider()

    def fake_get(self: object, *args: object, **kwargs: object) -> object:
        raise AssertionError("network should not be hit once cached")

    prov._cache = _PAYLOAD
    monkeypatch.setattr("httpx.Client.get", fake_get)

    prov.load_players()
    prov.fixtures()  # would raise if this re-fetched instead of reusing the cache
