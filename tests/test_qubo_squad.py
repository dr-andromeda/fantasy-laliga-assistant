from __future__ import annotations

import pytest

pytest.importorskip("qubo_forge")

from fantasy_assistant.model import Constraints, Player, Position, Squad, SquadPlayer
from fantasy_assistant.qubo_squad import (
    _slack_weights,
    build_pool,
    build_squad_qubo,
    compare_solvers,
)

CONSTRAINTS = Constraints(
    provider="test", squad_size=2, lineup_size=2,
    squad_by_position={Position.MID: 1, Position.FWD: 1},
)


def _player(pid: str, pos: Position, price: int = 5_000_000) -> Player:
    return Player(id=pid, name=pid, team="T", position=pos, price=price)


def test_slack_weights_cover_the_full_range() -> None:
    for capacity in (0, 1, 5, 7, 8, 100, 257):
        weights = _slack_weights(capacity)
        subsets = (s for r in range(len(weights) + 1) for s in _combinations(weights, r))
        achievable = {sum(subset) for subset in subsets}
        assert achievable.issuperset(range(0, capacity + 1))
        assert max(achievable, default=0) == capacity


def _combinations(items: list[int], r: int) -> list[tuple[int, ...]]:
    import itertools
    return list(itertools.combinations(items, r))


def test_build_pool_includes_owned_and_top_candidates_per_position() -> None:
    owned_mid = _player("owned_mid", Position.MID)
    owned_fwd = _player("owned_fwd", Position.FWD)
    squad = Squad(players=[SquadPlayer(player=owned_mid), SquadPlayer(player=owned_fwd)])

    market = [_player(f"mid_{i}", Position.MID) for i in range(5)] + [
        _player(f"fwd_{i}", Position.FWD) for i in range(5)
    ]
    universe = [owned_mid, owned_fwd, *market]
    expected = {p.id: float(10 - i) for i, p in enumerate(market)} | {
        owned_mid.id: 1.0, owned_fwd.id: 1.0
    }

    pool = build_pool(squad, universe, expected, candidates_per_position=2)
    pool_ids = {p.id for p in pool}

    assert owned_mid.id in pool_ids
    assert owned_fwd.id in pool_ids
    assert {"mid_0", "mid_1"}.issubset(pool_ids)  # the two best unowned MIDs
    assert "mid_4" not in pool_ids  # worst-ranked, beyond candidates_per_position=2


def test_build_squad_qubo_registers_expected_variables_and_terms() -> None:
    owned = _player("owned", Position.MID, price=5_000_000)
    squad = Squad(players=[SquadPlayer(player=owned)], budget_remaining=10_000_000)
    star_fwd = _player("star_fwd", Position.FWD, price=5_000_000)  # same price as "owned"
    universe = [owned, star_fwd]
    expected = {"owned": 3.0, "star_fwd": 10.0}

    pool = build_pool(squad, universe, expected, candidates_per_position=5)
    model = build_squad_qubo(pool, expected, CONSTRAINTS, squad.bankroll())

    assert "owned" in model.variables
    assert "star_fwd" in model.variables
    assert any(isinstance(v, tuple) and v[0] == "slack" for v in model.variables)
    # same price and each the sole candidate at their position -> their constraint-derived
    # terms are identical, so the gap is exactly the objective difference (10.0 - 3.0 = 7.0)
    assert model.linear["star_fwd"] == pytest.approx(model.linear["owned"] - 7.0)


def test_compare_solvers_finds_the_only_legal_squad() -> None:
    # exactly one MID and one FWD exist -> there is only one legal, affordable squad;
    # every solver (exhaustive, SA, tabu) must find it.
    mid = _player("mid", Position.MID, price=5_000_000)
    fwd = _player("fwd", Position.FWD, price=5_000_000)
    squad = Squad(players=[SquadPlayer(player=mid)], budget_remaining=10_000_000)
    universe = [mid, fwd]
    expected = {"mid": 5.0, "fwd": 8.0}

    report = compare_solvers(
        squad, universe, expected, CONSTRAINTS,
        candidates_per_position=5, exhaustive_candidates_per_position=5, seed=1,
    )

    assert report.exhaustive is not None
    for sel in (report.exhaustive, report.simulated_annealing, report.tabu):
        assert sel.feasible, sel.violations
        assert set(sel.player_ids) == {"mid", "fwd"}
        assert sel.total_expected == pytest.approx(13.0)


def test_compare_solvers_notes_when_exhaustive_is_skipped() -> None:
    owned = [_player(f"mid_{i}", Position.MID) for i in range(1)] + [
        _player(f"fwd_{i}", Position.FWD) for i in range(1)
    ]
    squad = Squad(players=[SquadPlayer(player=p) for p in owned], budget_remaining=50_000_000)
    market = [_player(f"mid_extra_{i}", Position.MID) for i in range(10)] + [
        _player(f"fwd_extra_{i}", Position.FWD) for i in range(10)
    ]
    universe = [*owned, *market]
    expected = {p.id: 5.0 for p in universe}

    report = compare_solvers(
        squad, universe, expected, CONSTRAINTS,
        candidates_per_position=10, exhaustive_candidates_per_position=10, seed=1,
    )

    assert report.exhaustive is None
    assert "Skipping the exhaustive solver" in report.note
