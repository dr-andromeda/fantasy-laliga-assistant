"""The baseline points predictor.

Expected fantasy points for a player over the next few gameweeks are

    expected_gw = form_rate * minutes_factor * fixture_factor(gw)

* ``form_rate`` — exponentially-weighted mean of recent per-gameweek points.
* ``minutes_factor`` — a 0..1 availability multiplier (from injury status for now;
  a real minutes model replaces it later).
* ``fixture_factor`` — softens or lifts the estimate for the specific opponent and
  home/away split, using a crude team-strength rating.

Nothing here is clever. It is the honest baseline every later model is measured
against, and every number it produces is explainable — see :meth:`Projection.explain`.
"""

from __future__ import annotations

import statistics
from importlib import resources

import yaml
from pydantic import BaseModel, Field

from fantasy_assistant.model import Fixture, Player, PlayerStatus, Squad


class PredictorConfig(BaseModel):
    form_half_life: float = 3.0
    status_minutes: dict[PlayerStatus, float] = Field(
        default_factory=lambda: {
            PlayerStatus.OK: 1.0,
            PlayerStatus.DOUBTFUL: 0.55,
            PlayerStatus.INJURED: 0.0,
            PlayerStatus.SUSPENDED: 0.0,
            PlayerStatus.UNKNOWN: 0.9,
        }
    )
    home_advantage: float = 0.08
    difficulty_scale: float = 0.25
    fixture_factor_min: float = 0.55
    fixture_factor_max: float = 1.45
    uncertainty_k: float = 1.0

    @classmethod
    def load(cls) -> PredictorConfig:
        text = resources.files("fantasy_assistant.config").joinpath("prediction.yaml").read_text(
            "utf-8"
        )
        data = yaml.safe_load(text) or {}
        return cls.model_validate(data)


class GameweekProjection(BaseModel):
    gameweek: int
    opponent: str | None
    is_home: bool | None
    fixture_factor: float
    expected: float


class Projection(BaseModel):
    player_id: str
    player_name: str
    horizon: int
    expected: float
    low: float
    high: float
    form_rate: float
    minutes_factor: float
    per_gameweek: list[GameweekProjection]

    def explain(self) -> str:
        head = (
            f"{self.player_name}: {self.expected:.1f} pts over {self.horizon} GW "
            f"(band {self.low:.1f}-{self.high:.1f})\n"
            f"  form rate {self.form_rate:.1f}/GW x minutes {self.minutes_factor:.2f}"
        )
        lines = [
            f"  GW{g.gameweek}: {'vs' if g.is_home else '@'} {g.opponent or 'avg fixture'} "
            f"x{g.fixture_factor:.2f}  ->  {g.expected:.1f}"
            for g in self.per_gameweek
        ]
        return "\n".join([head, *lines])


class SquadProjection(BaseModel):
    horizon: int
    per_player: list[Projection]
    lineup_expected: float
    lineup_low: float
    lineup_high: float
    captain_id: str | None
    captain_bonus: float


def _exp_weighted_mean(values: list[float], half_life: float) -> float:
    """Recent-first exponential mean: newest value has weight 1."""
    if not values:
        return 0.0
    decay = 0.5 ** (1.0 / half_life)
    num = den = 0.0
    for age, v in enumerate(reversed(values)):
        w = decay**age
        num += w * v
        den += w
    return num / den if den else 0.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def team_strength(players: list[Player]) -> dict[str, float]:
    """Rough per-club rating: z-scored mean of players' season points."""
    by_team: dict[str, list[int]] = {}
    for p in players:
        by_team.setdefault(p.team, []).append(p.total_points)
    means = {team: statistics.fmean(pts) for team, pts in by_team.items() if pts}
    if len(means) < 2:
        return dict.fromkeys(means, 0.0)
    mu = statistics.fmean(means.values())
    sigma = statistics.pstdev(means.values()) or 1.0
    return {team: (m - mu) / sigma for team, m in means.items()}


class PointsPredictor:
    def __init__(
        self,
        players: list[Player],
        fixtures: list[Fixture],
        config: PredictorConfig | None = None,
    ) -> None:
        self.config = config or PredictorConfig()
        self.fixtures = sorted(fixtures, key=lambda f: f.gameweek)
        self.strength = team_strength(players)

    def _fixture_factor(self, opponent: str, is_home: bool) -> float:
        opp = self.strength.get(opponent, 0.0)
        factor = 1.0 - self.config.difficulty_scale * opp
        venue = 1.0 + self.config.home_advantage if is_home else 1.0 - self.config.home_advantage
        factor *= venue
        return _clamp(factor, self.config.fixture_factor_min, self.config.fixture_factor_max)

    def _team_fixtures(self, team: str, horizon: int) -> list[Fixture]:
        seen: list[Fixture] = []
        for f in self.fixtures:
            if f.opponent_of(team) is not None:
                seen.append(f)
            if len({x.gameweek for x in seen}) >= horizon:
                break
        return seen[:horizon]

    def predict(self, player: Player, horizon: int = 3) -> Projection:
        cfg = self.config
        form_rate = _exp_weighted_mean(
            [float(x) for x in player.points_by_gameweek], cfg.form_half_life
        )
        minutes = cfg.status_minutes.get(player.status, 0.9)

        per_gw: list[GameweekProjection] = []
        fixtures = self._team_fixtures(player.team, horizon)
        for f in fixtures:
            opp = f.opponent_of(player.team)
            home = f.is_home(player.team)
            factor = self._fixture_factor(opp, home) if opp else 1.0
            per_gw.append(
                GameweekProjection(
                    gameweek=f.gameweek,
                    opponent=opp,
                    is_home=home,
                    fixture_factor=factor,
                    expected=form_rate * minutes * factor,
                )
            )
        # if the schedule is short, assume an average remaining fixture
        while len(per_gw) < horizon:
            per_gw.append(
                GameweekProjection(
                    gameweek=(per_gw[-1].gameweek + 1) if per_gw else 0,
                    opponent=None,
                    is_home=None,
                    fixture_factor=1.0,
                    expected=form_rate * minutes,
                )
            )

        expected = sum(g.expected for g in per_gw)
        sd = (
            statistics.pstdev([float(x) for x in player.points_by_gameweek])
            if len(player.points_by_gameweek) >= 2
            else max(form_rate * 0.5, 1.0)
        )
        spread = cfg.uncertainty_k * sd * (horizon**0.5) * minutes
        return Projection(
            player_id=player.id,
            player_name=player.name,
            horizon=horizon,
            expected=expected,
            low=max(0.0, expected - spread),
            high=expected + spread,
            form_rate=form_rate,
            minutes_factor=minutes,
            per_gameweek=per_gw,
        )

    def predict_squad(self, squad: Squad, horizon: int = 3) -> SquadProjection:
        rows = [self.predict(sp.player, horizon) for sp in squad.players]
        by_id = {r.player_id: r for r in rows}

        lineup = [sp for sp in squad.players if sp.in_lineup]
        lineup_expected = sum(by_id[sp.player.id].expected for sp in lineup)
        lineup_low = sum(by_id[sp.player.id].low for sp in lineup)
        lineup_high = sum(by_id[sp.player.id].high for sp in lineup)

        captain = next((sp for sp in squad.players if sp.is_captain), None)
        bonus = by_id[captain.player.id].expected if captain else 0.0

        return SquadProjection(
            horizon=horizon,
            per_player=rows,
            lineup_expected=lineup_expected + bonus,
            lineup_low=lineup_low + bonus,
            lineup_high=lineup_high + bonus,
            captain_id=captain.player.id if captain else None,
            captain_bonus=bonus,
        )
