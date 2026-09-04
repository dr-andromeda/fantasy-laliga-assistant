"""Pick the best legal starting XI and captain from the players a squad already owns.

The buy/sell transfer optimizer comes later; this only decides who starts.

It is exact by enumeration: LaLiga Fantasy allows only a handful of formations,
and within a formation the best choice per position is simply the top-k projected
players. So we try every valid formation, fill each greedily, and keep the one
with the highest projected total (captain counted twice).
"""

from __future__ import annotations

from pydantic import BaseModel

from fantasy_assistant.model import Constraints, Position, Squad
from fantasy_assistant.prediction import SquadProjection

_FILL_ORDER = (Position.GK, Position.DEF, Position.MID, Position.FWD)


class LineupPlan(BaseModel):
    formation: dict[Position, int]
    starters: list[str]          # player ids, grouped by position, best first
    bench: list[str]             # player ids, best projection first (first sub first)
    captain_id: str
    captain_name: str
    projected_points: float      # starters' expected + captain's expected again
    current_points: float | None
    improvement: float | None    # projected_points - current_points

    def formation_str(self) -> str:
        return "-".join(str(self.formation[p]) for p in _FILL_ORDER)


def valid_formations(constraints: Constraints) -> list[dict[Position, int]]:
    """Every position split that fills the XI within the min/max per position."""
    lo = constraints.lineup_min_by_position
    hi = constraints.lineup_max_by_position
    size = constraints.lineup_size

    def rng(pos: Position) -> range:
        return range(lo.get(pos, 0), hi.get(pos, size) + 1)

    out: list[dict[Position, int]] = []
    for gk in rng(Position.GK):
        for d in rng(Position.DEF):
            for m in rng(Position.MID):
                f = size - gk - d - m
                if f in rng(Position.FWD):
                    out.append(
                        {Position.GK: gk, Position.DEF: d, Position.MID: m, Position.FWD: f}
                    )
    return out


def best_lineup(
    squad: Squad, projection: SquadProjection, constraints: Constraints
) -> LineupPlan:
    exp = {p.player_id: p.expected for p in projection.per_player}
    name = {p.player_id: p.player_name for p in projection.per_player}
    return lineup_from_expected(squad, exp, name, constraints)


def lineup_from_expected(
    squad: Squad,
    expected: dict[str, float],
    names: dict[str, str],
    constraints: Constraints,
) -> LineupPlan:
    """The same exact-by-enumeration search as :func:`best_lineup`, driven by a plain
    ``player_id -> expected points`` mapping instead of a :class:`SquadProjection`.

    This is what lets :mod:`fantasy_assistant.backtest` reuse the *identical* optimizer
    for both "predicted lineup, ranked by forecast" and "hindsight-optimal lineup,
    ranked by what actually happened" -- only the mapping changes, not the logic.
    """
    exp = expected
    ranked: dict[Position, list[str]] = {pos: [] for pos in Position}
    for sp in squad.players:
        ranked[sp.player.position].append(sp.player.id)
    for pos in ranked:
        ranked[pos].sort(key=lambda pid: exp.get(pid, 0.0), reverse=True)

    best: tuple[float, dict[Position, int], list[str]] | None = None
    for formation in valid_formations(constraints):
        if any(len(ranked[pos]) < count for pos, count in formation.items()):
            continue
        starters = [pid for pos in _FILL_ORDER for pid in ranked[pos][: formation[pos]]]
        base = sum(exp.get(pid, 0.0) for pid in starters)
        captain = max(starters, key=lambda pid: exp.get(pid, 0.0))
        score = base + exp.get(captain, 0.0)
        if best is None or score > best[0]:
            best = (score, formation, starters)

    if best is None:
        raise ValueError("squad cannot field a legal XI (not enough players by position)")

    score, formation, starters = best
    starter_set = set(starters)
    bench = sorted(
        (sp.player.id for sp in squad.players if sp.player.id not in starter_set),
        key=lambda pid: exp.get(pid, 0.0),
        reverse=True,
    )
    captain_id = max(starters, key=lambda pid: exp.get(pid, 0.0))

    current = _score_current_xi(squad, exp, constraints.lineup_size)
    return LineupPlan(
        formation=formation,
        starters=starters,
        bench=bench,
        captain_id=captain_id,
        captain_name=names.get(captain_id, captain_id),
        projected_points=round(score, 2),
        current_points=None if current is None else round(current, 2),
        improvement=None if current is None else round(score - current, 2),
    )


def _score_current_xi(squad: Squad, exp: dict[str, float], lineup_size: int) -> float | None:
    """Projection of the squad's current XI, captained optimally within it.

    Returns ``None`` when the squad file doesn't flag a full XI, so there is
    nothing meaningful to compare against.
    """
    current = [sp.player.id for sp in squad.players if sp.in_lineup]
    if len(current) != lineup_size:
        return None
    base = sum(exp.get(pid, 0.0) for pid in current)
    return base + max(exp.get(pid, 0.0) for pid in current)
