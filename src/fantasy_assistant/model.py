"""Core domain model, shared by every provider.

Everything downstream — valuation, prediction, optimization, reporting — works on
these types, never on a provider's raw payload.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, NonNegativeInt


class Position(StrEnum):
    GK = "GK"
    DEF = "DEF"
    MID = "MID"
    FWD = "FWD"


class PlayerStatus(StrEnum):
    OK = "ok"
    DOUBTFUL = "doubtful"
    INJURED = "injured"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


class Player(BaseModel):
    """A LaLiga player as seen by a fantasy provider."""

    id: str
    name: str
    team: str
    position: Position
    price: NonNegativeInt = Field(description="Current market price, in euros")
    total_points: int = 0
    points_by_gameweek: list[int] = Field(default_factory=list)
    status: PlayerStatus = PlayerStatus.UNKNOWN
    provider: str = "unknown"

    def form(self, window: int = 5) -> float:
        """Mean fantasy points over the last ``window`` gameweeks played."""
        recent = self.points_by_gameweek[-window:]
        return sum(recent) / len(recent) if recent else 0.0

    def points_per_million(self) -> float:
        return self.total_points / (self.price / 1_000_000) if self.price else 0.0


class Fixture(BaseModel):
    gameweek: int
    home_team: str
    away_team: str

    def opponent_of(self, team: str) -> str | None:
        if team == self.home_team:
            return self.away_team
        if team == self.away_team:
            return self.home_team
        return None

    def is_home(self, team: str) -> bool:
        return team == self.home_team


class ScoringRules(BaseModel):
    """Points awarded per event. Loaded from a per-provider YAML file."""

    provider: str
    events: dict[str, float] = Field(default_factory=dict)

    def points_for(self, event: str) -> float:
        return self.events.get(event, 0.0)


class Constraints(BaseModel):
    """Squad-building rules for one provider / game mode."""

    provider: str
    squad_size: int = 15
    lineup_size: int = 11
    squad_by_position: dict[Position, int] = Field(default_factory=dict)
    lineup_min_by_position: dict[Position, int] = Field(default_factory=dict)
    lineup_max_by_position: dict[Position, int] = Field(default_factory=dict)
    max_players_per_club: int | None = None


class SquadPlayer(BaseModel):
    player: Player
    in_lineup: bool = True
    is_captain: bool = False


class Squad(BaseModel):
    """The user's team: a set of owned players plus spendable budget."""

    players: list[SquadPlayer] = Field(default_factory=list)
    budget_remaining: NonNegativeInt = 0

    @property
    def owned(self) -> list[Player]:
        return [sp.player for sp in self.players]

    def total_value(self) -> int:
        return sum(p.price for p in self.owned)

    def bankroll(self) -> int:
        """Total spend available if the whole squad were sold."""
        return self.total_value() + self.budget_remaining

    def by_position(self) -> dict[Position, list[Player]]:
        out: dict[Position, list[Player]] = {pos: [] for pos in Position}
        for p in self.owned:
            out[p.position].append(p)
        return out

    def validate_against(self, constraints: Constraints) -> list[str]:
        """Return a list of rule violations (empty means the squad is legal)."""
        problems: list[str] = []
        if len(self.players) != constraints.squad_size:
            problems.append(
                f"squad has {len(self.players)} players, expected {constraints.squad_size}"
            )

        counts = {pos: len(ps) for pos, ps in self.by_position().items()}
        for pos, expected in constraints.squad_by_position.items():
            if counts.get(pos, 0) != expected:
                problems.append(f"{pos.value}: have {counts.get(pos, 0)}, need {expected}")

        if constraints.max_players_per_club is not None:
            per_club: dict[str, int] = {}
            for p in self.owned:
                per_club[p.team] = per_club.get(p.team, 0) + 1
            for club, n in per_club.items():
                if n > constraints.max_players_per_club:
                    problems.append(
                        f"{n} players from {club} (max {constraints.max_players_per_club})"
                    )

        lineup = [sp for sp in self.players if sp.in_lineup]
        if lineup and len(lineup) != constraints.lineup_size:
            problems.append(f"lineup has {len(lineup)}, expected {constraints.lineup_size}")

        captains = [sp for sp in self.players if sp.is_captain]
        if len(captains) > 1:
            problems.append("more than one captain selected")

        return problems
