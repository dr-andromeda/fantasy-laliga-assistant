from __future__ import annotations

import pytest

from fantasy_assistant.model import Player, Position, Squad, SquadPlayer
from fantasy_assistant.optimize import best_lineup, valid_formations
from fantasy_assistant.prediction import Projection, SquadProjection
from fantasy_assistant.providers.laliga_fantasy import LaLigaFantasyProvider

CONSTRAINTS = LaLigaFantasyProvider(source="csv").constraints()


def _proj(pid: str, expected: float) -> Projection:
    return Projection(
        player_id=pid, player_name=pid, horizon=1, expected=expected,
        low=expected, high=expected, form_rate=expected, minutes_factor=1.0,
        per_gameweek=[],
    )


def _squad_projection(values: dict[str, float]) -> SquadProjection:
    rows = [_proj(pid, v) for pid, v in values.items()]
    return SquadProjection(
        horizon=1, per_player=rows,
        lineup_expected=0.0, lineup_low=0.0, lineup_high=0.0,
        captain_id=None, captain_bonus=0.0,
    )


def _player(pid: str, pos: Position) -> Player:
    return Player(id=pid, name=pid, team="T", position=pos, price=1_000_000)


# projections chosen so 1-3-5-2 is the unique best XI, MID "m0" the captain
PROJ = {
    "g0": 4, "g1": 1,
    "d0": 7, "d1": 6, "d2": 5, "d3": 1, "d4": 1,
    "m0": 12, "m1": 11, "m2": 10, "m3": 9, "m4": 3,
    "f0": 8, "f1": 2, "f2": 1,
}
_POS = {"g": Position.GK, "d": Position.DEF, "m": Position.MID, "f": Position.FWD}


def _build_squad(lineup_ids: set[str] | None) -> Squad:
    players = []
    for pid in PROJ:
        sp = SquadPlayer(
            player=_player(pid, _POS[pid[0]]),
            in_lineup=(lineup_ids is None) or (pid in lineup_ids),
        )
        players.append(sp)
    return Squad(players=players, budget_remaining=0)


def test_valid_formations_are_legal():
    forms = valid_formations(CONSTRAINTS)
    assert forms
    for f in forms:
        assert sum(f.values()) == 11
        assert f[Position.GK] == 1
        assert 3 <= f[Position.DEF] <= 5
        assert 2 <= f[Position.MID] <= 5
        assert 1 <= f[Position.FWD] <= 3
    shapes = {tuple(f[p] for p in Position) for f in forms}
    assert (1, 3, 5, 2) in shapes
    assert (1, 4, 4, 2) in shapes


def test_best_lineup_picks_the_optimal_xi_and_captain():
    squad = _build_squad(lineup_ids=None)  # all flagged; comparison uses first 11? no -> exactly 15
    plan = best_lineup(squad, _squad_projection(PROJ), CONSTRAINTS)

    assert plan.formation == {
        Position.GK: 1, Position.DEF: 3, Position.MID: 5, Position.FWD: 2
    }
    assert set(plan.starters) == {"g0", "d0", "d1", "d2", "m0", "m1", "m2", "m3", "m4", "f0", "f1"}
    assert plan.captain_id == "m0"
    # 4 + (7+6+5) + (12+11+10+9+3) + (8+2) = 77, captain adds m0 again -> 89
    assert plan.projected_points == pytest.approx(89.0)
    assert plan.bench == sorted(plan.bench, key=lambda pid: PROJ[pid], reverse=True)
    assert len(plan.bench) == 4


def test_improvement_is_non_negative_vs_a_flagged_xi():
    weak_xi = {"g1", "d3", "d4", "d2", "m4", "m3", "m2", "m1", "m0", "f1", "f2"}  # a legal 1-3-5-2
    squad = _build_squad(lineup_ids=weak_xi)
    plan = best_lineup(squad, _squad_projection(PROJ), CONSTRAINTS)
    assert plan.current_points is not None
    assert plan.improvement is not None
    assert plan.improvement >= 0


def test_current_points_is_none_when_no_full_xi_flagged():
    squad = _build_squad(lineup_ids={"g0", "d0", "m0"})  # only 3 flagged
    plan = best_lineup(squad, _squad_projection(PROJ), CONSTRAINTS)
    assert plan.current_points is None
    assert plan.improvement is None


def test_raises_when_squad_cannot_field_an_xi():
    only_outfield = {pid: v for pid, v in PROJ.items() if not pid.startswith("g")}
    squad = Squad(
        players=[SquadPlayer(player=_player(p, _POS[p[0]])) for p in only_outfield],
        budget_remaining=0,
    )
    with pytest.raises(ValueError, match="legal XI"):
        best_lineup(squad, _squad_projection(only_outfield), CONSTRAINTS)
