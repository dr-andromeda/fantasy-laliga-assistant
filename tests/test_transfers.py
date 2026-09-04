from __future__ import annotations

from fantasy_assistant.model import Player, Position, Squad, SquadPlayer
from fantasy_assistant.prediction import Projection
from fantasy_assistant.providers.laliga_fantasy import LaLigaFantasyProvider
from fantasy_assistant.transfers import suggest_transfers

CONSTRAINTS = LaLigaFantasyProvider(source="csv").constraints()


def _player(pid: str, pos: Position, team: str = "T", price: int = 5_000_000) -> Player:
    return Player(id=pid, name=pid, team=team, position=pos, price=price)


class _FakePredictor:
    """Deterministic stand-in for PointsPredictor: pid -> expected points."""

    def __init__(self, expected: dict[str, float]) -> None:
        self.expected = expected

    def predict(self, player: Player, horizon: int = 3) -> Projection:
        e = self.expected.get(player.id, 0.0)
        return Projection(
            player_id=player.id, player_name=player.name, horizon=horizon,
            expected=e, low=e, high=e, form_rate=e, minutes_factor=1.0, per_gameweek=[],
        )


def _squad(owned: list[Player], budget: int) -> Squad:
    return Squad(players=[SquadPlayer(player=p) for p in owned], budget_remaining=budget)


def test_suggests_a_clear_upgrade_within_budget() -> None:
    owned = [_player("mid_weak", Position.MID, price=5_000_000)]
    universe = [*owned, _player("mid_strong", Position.MID, price=6_000_000)]
    predictor = _FakePredictor({"mid_weak": 5.0, "mid_strong": 9.0})

    plan = suggest_transfers(
        _squad(owned, budget=2_000_000), universe, predictor, CONSTRAINTS,  # type: ignore[arg-type]
        horizon=3, max_transfers=1,
    )

    assert len(plan.moves) == 1
    move = plan.moves[0]
    assert move.sell_id == "mid_weak"
    assert move.buy_id == "mid_strong"
    assert plan.total_gain == 4.0
    assert plan.net_cost == 1_000_000
    assert plan.budget_after == 1_000_000
    assert plan.overdraft == 0


def test_rejects_an_upgrade_that_does_not_fit_the_budget() -> None:
    owned = [_player("mid_weak", Position.MID, price=5_000_000)]
    universe = [*owned, _player("mid_strong", Position.MID, price=20_000_000)]
    predictor = _FakePredictor({"mid_weak": 5.0, "mid_strong": 9.0})

    plan = suggest_transfers(
        _squad(owned, budget=1_000_000), universe, predictor, CONSTRAINTS,  # type: ignore[arg-type]
        horizon=3, max_transfers=1,
    )

    assert plan.moves == []
    assert plan.total_gain == 0.0


def test_allow_overdraft_permits_an_unaffordable_upgrade_and_flags_it() -> None:
    owned = [_player("mid_weak", Position.MID, price=5_000_000)]
    universe = [*owned, _player("mid_strong", Position.MID, price=20_000_000)]
    predictor = _FakePredictor({"mid_weak": 5.0, "mid_strong": 9.0})

    plan = suggest_transfers(
        _squad(owned, budget=1_000_000), universe, predictor, CONSTRAINTS,  # type: ignore[arg-type]
        horizon=3, max_transfers=1, allow_overdraft=True,
    )

    assert len(plan.moves) == 1
    assert plan.budget_after == -14_000_000
    assert plan.overdraft == 14_000_000


def test_never_buys_the_same_target_twice() -> None:
    owned = [
        _player("mid_a", Position.MID, price=5_000_000),
        _player("mid_b", Position.MID, price=5_000_000),
    ]
    universe = [*owned, _player("mid_star", Position.MID, price=5_000_000)]
    predictor = _FakePredictor({"mid_a": 3.0, "mid_b": 3.0, "mid_star": 10.0})

    plan = suggest_transfers(
        _squad(owned, budget=10_000_000), universe, predictor, CONSTRAINTS,  # type: ignore[arg-type]
        horizon=3, max_transfers=2,
    )

    assert len({m.buy_id for m in plan.moves}) == len(plan.moves)
    assert plan.moves[0].buy_id == "mid_star"


def test_max_transfers_caps_the_number_of_moves() -> None:
    owned = [_player(f"mid_{i}", Position.MID, price=5_000_000) for i in range(3)]
    universe = [*owned, *(_player(f"mid_new_{i}", Position.MID, price=5_000_000) for i in range(3))]
    predictor = _FakePredictor(
        {f"mid_{i}": 3.0 for i in range(3)} | {f"mid_new_{i}": 8.0 for i in range(3)}
    )

    plan = suggest_transfers(
        _squad(owned, budget=100_000_000), universe, predictor, CONSTRAINTS,  # type: ignore[arg-type]
        horizon=3, max_transfers=2,
    )

    assert len(plan.moves) == 2


def test_only_considers_same_position_replacements() -> None:
    owned = [_player("gk_weak", Position.GK, price=5_000_000)]
    universe = [*owned, _player("fwd_star", Position.FWD, price=5_000_000)]
    predictor = _FakePredictor({"gk_weak": 3.0, "fwd_star": 10.0})

    plan = suggest_transfers(
        _squad(owned, budget=10_000_000), universe, predictor, CONSTRAINTS,  # type: ignore[arg-type]
        horizon=3, max_transfers=1,
    )

    assert plan.moves == []


def test_respects_max_players_per_club() -> None:
    from fantasy_assistant.model import Constraints

    capped = Constraints(
        provider="laliga", squad_size=2, lineup_size=2,
        squad_by_position={Position.MID: 1, Position.DEF: 1}, max_players_per_club=1,
    )
    owned = [
        _player("mid_weak", Position.MID, team="Home FC", price=5_000_000),
        _player("anchor_def", Position.DEF, team="Away FC", price=5_000_000),
    ]
    universe = [
        *owned,
        # the top gain, but Away FC already has anchor_def -> would push it to 2, over the cap
        _player("mid_from_away", Position.MID, team="Away FC", price=5_000_000),
        # a smaller gain, but from a club with no players yet -> allowed
        _player("mid_from_third", Position.MID, team="Third FC", price=5_000_000),
    ]
    predictor = _FakePredictor(
        {"mid_weak": 3.0, "anchor_def": 5.0, "mid_from_away": 10.0, "mid_from_third": 9.0}
    )

    plan = suggest_transfers(
        _squad(owned, budget=10_000_000), universe, predictor, capped,  # type: ignore[arg-type]
        horizon=3, max_transfers=1,
    )

    assert len(plan.moves) == 1
    assert plan.moves[0].buy_id == "mid_from_third"


def test_explain_reports_moves_and_overdraft_warning() -> None:
    owned = [_player("mid_weak", Position.MID, price=5_000_000)]
    universe = [*owned, _player("mid_strong", Position.MID, price=20_000_000)]
    predictor = _FakePredictor({"mid_weak": 5.0, "mid_strong": 9.0})

    plan = suggest_transfers(
        _squad(owned, budget=1_000_000), universe, predictor, CONSTRAINTS,  # type: ignore[arg-type]
        horizon=3, max_transfers=1, allow_overdraft=True,
    )
    text = plan.explain()
    assert "mid_weak" in text
    assert "mid_strong" in text
    assert "WARNING" in text


def test_empty_plan_explains_itself() -> None:
    owned = [_player("mid_weak", Position.MID, price=5_000_000)]
    predictor = _FakePredictor({"mid_weak": 5.0})
    plan = suggest_transfers(
        _squad(owned, budget=1_000_000), owned, predictor, CONSTRAINTS,  # type: ignore[arg-type]
        horizon=3, max_transfers=1,
    )
    assert plan.moves == []
    assert "No transfer" in plan.explain()
