"""Walk-forward backtest of the lineup + captain recommendation, against two
baselines: a static "do nothing" XI, and a hindsight-optimal upper bound.

**What this backtests, and what it doesn't.** Every player carries a trailing
history of recent gameweek points (``Player.points_by_gameweek``). This walks
that shared history week by week; at each step ``g`` it predicts using only
games *before* ``g`` (an expanding window, no lookahead) and scores the pick
against what actually happened in game ``g``. That is a legitimate,
leak-free evaluation of the **lineup and captain choice** -- but it is
deliberately narrower than the live predictor in :mod:`fantasy_assistant.prediction`:
there is no historical fixture-difficulty or per-week injury status to draw
on here (only the *current* squad snapshot is available), so this uses form
alone. It does not touch transfers either -- that needs a season of price
history this project doesn't have. Treat the numbers as "how good is the
lineup-picking logic on its own", not a claim about the full tool.

**Three arms, one optimizer.** "Recommended" and "hindsight" are the *same*
call to :func:`fantasy_assistant.optimize.lineup_from_expected` -- only the
points mapping changes (forecast vs. the actual result). That is what makes
hindsight a meaningful upper bound rather than a different algorithm:
whatever gap remains is exactly the cost of not knowing the future, not a
difference in how the two picks were made. "Static" never re-optimizes: it
is whatever XI and captain the squad file already flags, replayed unchanged.
"""

from __future__ import annotations

from pydantic import BaseModel

from fantasy_assistant.model import Constraints, Squad
from fantasy_assistant.optimize import lineup_from_expected
from fantasy_assistant.prediction import _exp_weighted_mean


class GameweekResult(BaseModel):
    gameweek_index: int
    recommended_points: float
    static_points: float
    hindsight_points: float
    recommended_captain: str
    hindsight_captain: str


class BacktestReport(BaseModel):
    weeks: list[GameweekResult]
    recommended_total: float
    static_total: float
    hindsight_total: float

    @property
    def edge_over_static(self) -> float:
        return round(self.recommended_total - self.static_total, 1)

    @property
    def capture_rate(self) -> float | None:
        """Share of the hindsight-optimal points the recommendation captured."""
        if self.hindsight_total <= 0:
            return None
        return round(self.recommended_total / self.hindsight_total, 3)

    def explain(self) -> str:
        lines = ["GW  Recommended  Static  Hindsight"]
        for w in self.weeks:
            lines.append(
                f"{w.gameweek_index:>3} {w.recommended_points:>12.1f} "
                f"{w.static_points:>7.1f} {w.hindsight_points:>10.1f}"
            )
        lines.append(
            f"\nTotals: recommended {self.recommended_total:.1f}  ·  "
            f"static {self.static_total:.1f}  ·  hindsight {self.hindsight_total:.1f}"
        )
        lines.append(f"Edge over doing nothing: {self.edge_over_static:+.1f} pts")
        if self.capture_rate is not None:
            lines.append(f"Captured {self.capture_rate * 100:.0f}% of the hindsight-optimal points")
        return "\n".join(lines)


def run_backtest(
    squad: Squad,
    constraints: Constraints,
    *,
    form_half_life: float = 3.0,
    min_history: int = 2,
) -> BacktestReport:
    owned = squad.owned
    if not owned:
        raise ValueError("squad has no players to backtest")

    n_weeks = min(len(p.points_by_gameweek) for p in owned)
    if n_weeks <= min_history:
        raise ValueError(
            f"not enough shared gameweek history to backtest: every owned player needs "
            f"more than {min_history} recorded gameweeks (shortest is {n_weeks})"
        )

    static_starters = [sp.player.id for sp in squad.players if sp.in_lineup]
    if len(static_starters) != constraints.lineup_size:
        raise ValueError(
            f"squad.yaml must flag a full legal XI ({constraints.lineup_size} players) "
            "to backtest the static baseline"
        )
    static_captain = next((sp.player.id for sp in squad.players if sp.is_captain), None)
    if static_captain is None:
        raise ValueError("squad.yaml must flag a captain to backtest the static baseline")

    names = {p.id: p.name for p in owned}
    weeks: list[GameweekResult] = []

    for g in range(min_history, n_weeks):
        actual = {p.id: float(p.points_by_gameweek[g]) for p in owned}
        forecast = {
            p.id: _exp_weighted_mean(
                [float(x) for x in p.points_by_gameweek[:g]], form_half_life
            )
            for p in owned
        }

        recommended = lineup_from_expected(squad, forecast, names, constraints)
        hindsight = lineup_from_expected(squad, actual, names, constraints)

        rec_points = (
            sum(actual[pid] for pid in recommended.starters) + actual[recommended.captain_id]
        )
        static_points = sum(actual[pid] for pid in static_starters) + actual[static_captain]
        hindsight_points = (
            sum(actual[pid] for pid in hindsight.starters) + actual[hindsight.captain_id]
        )

        weeks.append(
            GameweekResult(
                gameweek_index=g,
                recommended_points=round(rec_points, 1),
                static_points=round(static_points, 1),
                hindsight_points=round(hindsight_points, 1),
                recommended_captain=names[recommended.captain_id],
                hindsight_captain=names[hindsight.captain_id],
            )
        )

    return BacktestReport(
        weeks=weeks,
        recommended_total=round(sum(w.recommended_points for w in weeks), 1),
        static_total=round(sum(w.static_points for w in weeks), 1),
        hindsight_total=round(sum(w.hindsight_points for w in weeks), 1),
    )
