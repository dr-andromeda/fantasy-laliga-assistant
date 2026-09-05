from __future__ import annotations

import pytest

from fantasy_assistant.model import Fixture, Player, PlayerStatus, Position, Squad, SquadPlayer
from fantasy_assistant.prediction import (
    PointsPredictor,
    PredictorConfig,
    _exp_weighted_mean,
    _is_rotation_risk,
    team_strength,
)
from fantasy_assistant.providers.laliga_fantasy import LaLigaFantasyProvider


@pytest.fixture
def fixtures() -> list[Fixture]:
    return LaLigaFantasyProvider(source="csv").fixtures(upcoming=5)


def _player(
    name: str, team: str, pos: Position, history: list[int], status: PlayerStatus = PlayerStatus.OK
) -> Player:
    return Player(id=name, name=name, team=team, position=pos, price=5_000_000,
                  total_points=sum(history), points_by_gameweek=history, status=status)


def test_exp_weighted_mean_favours_recent() -> None:
    plain = 4.0  # mean of [2, 4, 6]
    weighted = _exp_weighted_mean([2, 4, 6], half_life=1.0)
    assert plain < weighted < 6.0


def test_exp_weighted_mean_of_nothing_is_zero() -> None:
    assert _exp_weighted_mean([], half_life=3.0) == 0.0


def test_team_strength_ranks_and_centres(universe: list[Player]) -> None:
    strength = team_strength(universe)
    assert strength["Costa Verde CF"] > strength["Najar Balompie"]
    assert abs(sum(strength.values())) < 1e-9  # z-scores sum to ~0


def test_config_loads_from_yaml() -> None:
    cfg = PredictorConfig.load()
    assert cfg.status_minutes[PlayerStatus.INJURED] == 0.0
    assert cfg.status_minutes[PlayerStatus.OK] == 1.0


def test_injured_player_projects_zero(universe: list[Player], fixtures: list[Fixture]) -> None:
    pred = PointsPredictor(universe, fixtures)
    injured = _player("X", "Costa Verde CF", Position.FWD, [10, 12, 9], PlayerStatus.INJURED)
    proj = pred.predict(injured, horizon=3)
    assert proj.expected == 0.0
    assert proj.minutes_factor == 0.0


def test_better_form_projects_higher(universe: list[Player], fixtures: list[Fixture]) -> None:
    pred = PointsPredictor(universe, fixtures)
    hot = _player("Hot", "Costa Verde CF", Position.MID, [10, 11, 12])
    cold = _player("Cold", "Costa Verde CF", Position.MID, [2, 3, 2])
    assert pred.predict(hot).expected > pred.predict(cold).expected


def test_fixture_factor_reacts_to_opponent_and_venue(
    universe: list[Player], fixtures: list[Fixture]
) -> None:
    pred = PointsPredictor(universe, fixtures)
    strong, weak = "Costa Verde CF", "Najar Balompie"
    assert pred._fixture_factor(strong, is_home=False) < pred._fixture_factor(weak, is_home=False)
    assert pred._fixture_factor(strong, is_home=True) > pred._fixture_factor(strong, is_home=False)


def test_projection_has_one_row_per_gameweek_and_sums(
    universe: list[Player], fixtures: list[Fixture]
) -> None:
    pred = PointsPredictor(universe, fixtures)
    p = _player("Y", "Rio Sella SD", Position.FWD, [6, 7, 5, 8])
    proj = pred.predict(p, horizon=4)
    assert len(proj.per_gameweek) == 4
    assert proj.expected == pytest.approx(sum(g.expected for g in proj.per_gameweek))
    assert proj.low <= proj.expected <= proj.high


def test_squad_projection_counts_lineup_and_captain(
    universe: list[Player], fixtures: list[Fixture]
) -> None:
    pred = PointsPredictor(universe, fixtures)
    a = _player("A", "Costa Verde CF", Position.MID, [8, 9, 10])
    b = _player("B", "Almaden CD", Position.FWD, [7, 6, 8])
    bench = _player("C", "Rio Sella SD", Position.DEF, [3, 4, 3])
    squad = Squad(players=[
        SquadPlayer(player=a, in_lineup=True, is_captain=True),
        SquadPlayer(player=b, in_lineup=True),
        SquadPlayer(player=bench, in_lineup=False),
    ])
    sp = pred.predict_squad(squad, horizon=3)

    per = {r.player_id: r for r in sp.per_player}
    expected_no_bench = per["A"].expected + per["B"].expected + per["A"].expected  # captain doubled
    assert sp.lineup_expected == pytest.approx(expected_no_bench)
    assert sp.captain_bonus == pytest.approx(per["A"].expected)
    assert sp.captain_id == "A"


def test_explain_is_readable(universe: list[Player], fixtures: list[Fixture]) -> None:
    pred = PointsPredictor(universe, fixtures)
    proj = pred.predict(_player("Z", "Montoro CF", Position.MID, [7, 8, 6]), horizon=2)
    text = proj.explain()
    assert "Z:" in text
    assert "GW" in text


def test_is_rotation_risk_looks_only_at_the_recent_window() -> None:
    assert not _is_rotation_risk([8, 6, 7], window=3, min_zeros=2)
    assert _is_rotation_risk([8, 0, 0], window=3, min_zeros=2)
    # the zeros are old news -- the last 3 games were all starts
    assert not _is_rotation_risk([0, 0, 8, 6, 7], window=3, min_zeros=2)


def test_is_rotation_risk_needs_enough_history_to_judge() -> None:
    # only one gameweek recorded, even though it's a zero -- not enough of a
    # pattern to call yet
    assert not _is_rotation_risk([0], window=3, min_zeros=2)


def test_rotation_risk_dampens_minutes_factor(
    universe: list[Player], fixtures: list[Fixture]
) -> None:
    pred = PointsPredictor(universe, fixtures)
    # recently steady starter vs. someone whose last two games were unused-sub zeros
    playing = _player("Playing", "Costa Verde CF", Position.MID, [8, 6, 7])
    benched = _player("Benched", "Costa Verde CF", Position.MID, [8, 6, 7, 0, 0])

    playing_proj = pred.predict(playing, horizon=2)
    benched_proj = pred.predict(benched, horizon=2)

    assert not playing_proj.rotation_risk
    assert benched_proj.rotation_risk
    assert benched_proj.minutes_factor == pytest.approx(
        playing_proj.minutes_factor * PredictorConfig().rotation_risk_factor
    )


def test_rotation_risk_does_not_override_injury_status(
    universe: list[Player], fixtures: list[Fixture]
) -> None:
    pred = PointsPredictor(universe, fixtures)
    injured = _player(
        "InjuredAndRotated", "Costa Verde CF", Position.FWD, [0, 0, 0], PlayerStatus.INJURED
    )
    proj = pred.predict(injured)
    assert proj.rotation_risk
    assert proj.minutes_factor == 0.0
    assert proj.expected == 0.0


def test_explain_flags_rotation_risk(universe: list[Player], fixtures: list[Fixture]) -> None:
    pred = PointsPredictor(universe, fixtures)
    benched = _player("Benched2", "Costa Verde CF", Position.MID, [8, 6, 7, 0, 0])
    assert "rotation risk" in pred.predict(benched).explain()
