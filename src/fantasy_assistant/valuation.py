"""A no-model valuation of a squad: prices, season points, recent form, value.

This is the v1 baseline. The points predictor and the transfer optimizer build on
these same rows later; for now it is what the ``fla squad show`` command prints.
"""

from __future__ import annotations

from pydantic import BaseModel

from fantasy_assistant.model import Fixture, Player, Squad


class PlayerValuation(BaseModel):
    player: Player
    form: float
    points_per_million: float
    next_opponent: str | None = None
    next_is_home: bool | None = None

    @property
    def price_millions(self) -> float:
        return self.player.price / 1_000_000


class SquadValuation(BaseModel):
    rows: list[PlayerValuation]
    total_value: int
    budget_remaining: int
    bankroll: int
    season_points: int
    violations: list[str]

    @property
    def is_legal(self) -> bool:
        return not self.violations


def _next_fixture_for(team: str, fixtures: list[Fixture]) -> Fixture | None:
    upcoming = sorted(
        (f for f in fixtures if f.opponent_of(team) is not None),
        key=lambda f: f.gameweek,
    )
    return upcoming[0] if upcoming else None


def value_player(player: Player, fixtures: list[Fixture], form_window: int = 5) -> PlayerValuation:
    fixture = _next_fixture_for(player.team, fixtures)
    return PlayerValuation(
        player=player,
        form=player.form(form_window),
        points_per_million=player.points_per_million(),
        next_opponent=fixture.opponent_of(player.team) if fixture else None,
        next_is_home=fixture.is_home(player.team) if fixture else None,
    )


def value_squad(
    squad: Squad,
    fixtures: list[Fixture],
    violations: list[str] | None = None,
    form_window: int = 5,
) -> SquadValuation:
    rows = [value_player(p, fixtures, form_window) for p in squad.owned]
    return SquadValuation(
        rows=rows,
        total_value=squad.total_value(),
        budget_remaining=squad.budget_remaining,
        bankroll=squad.bankroll(),
        season_points=sum(p.total_points for p in squad.owned),
        violations=violations or [],
    )
