from __future__ import annotations

import pytest

from fantasy_assistant.model import Constraints, Player, Position, Squad, SquadPlayer

_counter = iter(range(1, 10_000))


def make_player(pos: Position, price: int = 5_000_000, *, team: str = "T") -> Player:
    n = next(_counter)
    return Player(id=str(n), name=f"P{n}", team=team, position=pos, price=price)


def test_form_is_mean_of_recent_gameweeks() -> None:
    p = Player(id="1", name="A", team="T", position=Position.MID,
               price=1, points_by_gameweek=[2, 4, 6, 8, 10, 12])
    assert p.form(window=3) == pytest.approx(10.0)
    assert p.form(window=10) == pytest.approx(7.0)


def test_form_of_new_player_is_zero() -> None:
    p = Player(id="1", name="A", team="T", position=Position.FWD, price=1)
    assert p.form() == 0.0


def test_points_per_million() -> None:
    p = Player(id="1", name="A", team="T", position=Position.DEF, price=10_000_000, total_points=60)
    assert p.points_per_million() == pytest.approx(6.0)


def test_squad_aggregates() -> None:
    squad = Squad(
        players=[
            SquadPlayer(player=make_player(Position.GK, 8_000_000)),
            SquadPlayer(player=make_player(Position.FWD, 20_000_000)),
        ],
        budget_remaining=2_000_000,
    )
    assert squad.total_value() == 28_000_000
    assert squad.bankroll() == 30_000_000
    assert len(squad.by_position()[Position.GK]) == 1


def test_validation_flags_wrong_shape_and_captains() -> None:
    constraints = Constraints(
        provider="test", squad_size=2, lineup_size=2,
        squad_by_position={Position.GK: 1, Position.FWD: 1},
        max_players_per_club=1,
    )
    squad = Squad(players=[
        SquadPlayer(player=make_player(Position.FWD, team="A"), is_captain=True),
        SquadPlayer(player=make_player(Position.FWD, team="A"), is_captain=True),
    ])
    problems = squad.validate_against(constraints)
    assert any("GK" in p for p in problems)
    assert any("more than one captain" in p for p in problems)
    assert any("from A" in p for p in problems)


def test_valid_squad_has_no_violations(universe: list[Player]) -> None:
    # build a legal 2/5/5/3 squad straight from the sample universe
    from fantasy_assistant.providers.laliga_fantasy import LaLigaFantasyProvider

    constraints = LaLigaFantasyProvider(source="csv").constraints()
    picked: list[SquadPlayer] = []
    for pos, need in constraints.squad_by_position.items():
        for p in [pl for pl in universe if pl.position is pos][:need]:
            picked.append(SquadPlayer(player=p, in_lineup=False))
    for sp in picked[:11]:
        sp.in_lineup = True
    squad = Squad(players=picked)
    assert squad.validate_against(constraints) == []
