from __future__ import annotations

import pytest

from fantasy_assistant.backtest import run_backtest
from fantasy_assistant.model import Constraints, Player, Position, Squad, SquadPlayer

CONSTRAINTS = Constraints(
    provider="test", squad_size=2, lineup_size=1,
    squad_by_position={Position.MID: 2}, lineup_max_by_position={Position.MID: 1},
)


def _player(pid: str, history: list[int]) -> Player:
    return Player(
        id=pid, name=pid, team="T", position=Position.MID, price=5_000_000,
        points_by_gameweek=history,
    )


def _squad(players: list[Player], starter: str, captain: str) -> Squad:
    return Squad(
        players=[
            SquadPlayer(player=p, in_lineup=(p.id == starter), is_captain=(p.id == captain))
            for p in players
        ]
    )


def test_recommended_follows_recent_form() -> None:
    # "hot" has been scoring well every week; "cold" hasn't. Form-based prediction
    # should always start (and, with a one-man lineup, automatically captain) "hot".
    hot = _player("hot", [10, 10, 10, 10, 10])
    cold = _player("cold", [1, 1, 1, 1, 1])
    squad = _squad([hot, cold], starter="cold", captain="cold")

    report = run_backtest(squad, CONSTRAINTS, min_history=2)

    assert len(report.weeks) == 3  # gameweeks 2, 3, 4
    for w in report.weeks:
        assert w.recommended_points == 20.0  # hot (10) started and captained (doubled)
        assert w.recommended_captain == "hot"


def test_static_baseline_never_changes_pick() -> None:
    hot = _player("hot", [10, 10, 10, 10])
    cold = _player("cold", [1, 1, 1, 1])
    squad = _squad([hot, cold], starter="cold", captain="cold")

    report = run_backtest(squad, CONSTRAINTS, min_history=2)

    assert all(w.static_points == 2.0 for w in report.weeks)  # cold (1) started and captained
    assert report.static_total == pytest.approx(2.0 * len(report.weeks))


def test_hindsight_is_always_at_least_as_good_as_recommended_and_static() -> None:
    # scores fluctuate so form-based prediction can't always call the actual winner
    hot = _player("hot", [10, 1, 10, 1, 10])
    cold = _player("cold", [1, 10, 1, 10, 1])
    squad = _squad([hot, cold], starter="hot", captain="hot")

    report = run_backtest(squad, CONSTRAINTS, min_history=2)

    for w in report.weeks:
        assert w.hindsight_points >= w.recommended_points
        assert w.hindsight_points >= w.static_points
    assert report.hindsight_total >= report.recommended_total
    assert report.hindsight_total >= report.static_total


def test_capture_rate_and_edge_over_static() -> None:
    hot = _player("hot", [10, 10, 10, 10])
    cold = _player("cold", [1, 1, 1, 1])
    squad = _squad([hot, cold], starter="cold", captain="cold")

    report = run_backtest(squad, CONSTRAINTS, min_history=2)

    assert report.capture_rate == pytest.approx(1.0)  # form always finds "hot" here
    assert report.edge_over_static > 0


def test_raises_when_history_is_too_short() -> None:
    hot = _player("hot", [10, 10])
    cold = _player("cold", [1, 1])
    squad = _squad([hot, cold], starter="cold", captain="cold")

    with pytest.raises(ValueError, match="not enough shared gameweek history"):
        run_backtest(squad, CONSTRAINTS, min_history=2)


def test_raises_without_a_full_static_xi() -> None:
    hot = _player("hot", [10, 10, 10, 10])
    cold = _player("cold", [1, 1, 1, 1])
    squad = Squad(players=[SquadPlayer(player=hot, in_lineup=True), SquadPlayer(player=cold)])

    with pytest.raises(ValueError, match="full legal XI"):
        run_backtest(squad, CONSTRAINTS, min_history=2)


def test_raises_without_a_flagged_captain() -> None:
    hot = _player("hot", [10, 10, 10, 10])
    cold = _player("cold", [1, 1, 1, 1])
    squad = Squad(players=[
        SquadPlayer(player=hot, in_lineup=True), SquadPlayer(player=cold, in_lineup=False),
    ])

    with pytest.raises(ValueError, match="captain"):
        run_backtest(squad, CONSTRAINTS, min_history=2)


def test_explain_is_readable() -> None:
    hot = _player("hot", [10, 10, 10, 10])
    cold = _player("cold", [1, 1, 1, 1])
    squad = _squad([hot, cold], starter="cold", captain="cold")

    report = run_backtest(squad, CONSTRAINTS, min_history=2)
    text = report.explain()
    assert "recommended" in text
    assert "hindsight" in text
