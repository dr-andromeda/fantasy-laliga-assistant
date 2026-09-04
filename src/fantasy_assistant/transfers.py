"""Suggest transfers: which owned players to sell and which to buy, to raise
projected points over the horizon.

**The budget rule.** LaLiga Fantasy lets a signing push your balance negative
with no cap on how far -- but if you're still in the red when the *next*
gameweek kicks off, you score **zero points that gameweek**, whatever your
lineup (verified against the official rules, Sept 2026:
https://fantasy-marca.helpscoutdocs.com/article/381-reglas-del-juego and
https://laligafantasy.zendesk.com/hc/en-us/articles/360007533594). That risk
dwarfs almost any transfer gain, so by default this optimizer treats the
budget as a hard cap: total net spend must not exceed cash on hand. Pass
``allow_overdraft=True`` only if you intend to clear the gap yourself before
the deadline -- the plan then reports exactly how far short you are.

**Why only same-position swaps.** A LaLiga Fantasy squad is position-locked
(2 GK / 5 DEF / 5 MID / 3 FWD in classic mode) -- swapping a player for one in
the same position is the only kind of transfer that keeps the squad legal on
its own, with no second move required to rebalance. Restructuring across
positions is out of scope for v1.

**The search.** For each owned player, keep only the top
``candidates_per_slot`` same-position replacements by projected gain (a
pruned candidate set, not the full universe). Then branch-and-bound over
subsets of size <= ``max_transfers``, picking at most one replacement per
owned player and never buying the same target twice, for the highest total
gain that fits the budget and the per-club cap. The search is exact over
that pruned set: with a handful of transfers and a handful of candidates
each, the branch-and-bound explores it completely.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from fantasy_assistant.model import Constraints, Player, Position, Squad
from fantasy_assistant.prediction import PointsPredictor


class TransferMove(BaseModel):
    sell_id: str
    sell_name: str
    sell_team: str
    buy_id: str
    buy_name: str
    buy_team: str
    position: Position
    sell_price: int
    buy_price: int
    net_cost: int  # buy_price - sell_price; negative frees up cash
    sell_expected: float
    buy_expected: float
    gain: float  # buy_expected - sell_expected, over the horizon


class TransferPlan(BaseModel):
    horizon: int
    moves: list[TransferMove]
    total_gain: float
    net_cost: int
    budget_before: int
    budget_after: int
    overdraft: int  # max(0, -budget_after); 0 means the plan is affordable outright

    def explain(self) -> str:
        if not self.moves:
            return f"No transfer over {self.horizon} GW beats keeping your squad as is."
        lines = [
            f"{len(self.moves)} transfer(s), +{self.total_gain:.1f} pts over {self.horizon} GW:"
        ]
        for m in self.moves:
            sign = "+" if m.net_cost >= 0 else ""
            lines.append(
                f"  OUT {m.sell_name} ({m.sell_team}, {m.sell_expected:.1f} pts)  ->  "
                f"IN {m.buy_name} ({m.buy_team}, {m.buy_expected:.1f} pts)   "
                f"{sign}{m.net_cost / 1_000_000:.2f}M, +{m.gain:.1f} pts"
            )
        lines.append(f"Net spend: {self.net_cost / 1_000_000:+.2f}M")
        if self.overdraft:
            lines.append(
                f"WARNING: ends {self.overdraft / 1_000_000:.2f}M short. Clear it before the "
                "next gameweek deadline or you score ZERO points that gameweek."
            )
        return "\n".join(lines)


def suggest_transfers(
    squad: Squad,
    universe: list[Player],
    predictor: PointsPredictor,
    constraints: Constraints,
    *,
    horizon: int = 3,
    max_transfers: int = 3,
    candidates_per_slot: int = 5,
    allow_overdraft: bool = False,
) -> TransferPlan:
    owned_ids = {p.id for p in squad.owned}
    owned_expected = {p.id: predictor.predict(p, horizon).expected for p in squad.owned}

    by_position: dict[Position, list[Player]] = {pos: [] for pos in Position}
    for p in universe:
        if p.id not in owned_ids:
            by_position[p.position].append(p)

    candidate_expected: dict[str, float] = {}

    def expected_of(p: Player) -> float:
        if p.id not in candidate_expected:
            candidate_expected[p.id] = predictor.predict(p, horizon).expected
        return candidate_expected[p.id]

    slots = list(squad.players)
    move_options: list[list[TransferMove]] = []
    for sp in slots:
        owner = sp.player
        scored = sorted(
            (
                (expected_of(c) - owned_expected[owner.id], c)
                for c in by_position[owner.position]
            ),
            key=lambda t: t[0],
            reverse=True,
        )
        move_options.append(
            [
                TransferMove(
                    sell_id=owner.id, sell_name=owner.name, sell_team=owner.team,
                    buy_id=c.id, buy_name=c.name, buy_team=c.team,
                    position=owner.position,
                    sell_price=owner.price, buy_price=c.price,
                    net_cost=c.price - owner.price,
                    sell_expected=round(owned_expected[owner.id], 2),
                    buy_expected=round(expected_of(c), 2),
                    gain=round(gain, 2),
                )
                for gain, c in scored[:candidates_per_slot]
                if gain > 0
            ]
        )

    budget = squad.budget_remaining
    max_club = constraints.max_players_per_club
    club_counts = Counter(p.team for p in squad.owned)

    # search the best-first-per-slot for tighter pruning
    order = sorted(
        range(len(slots)),
        key=lambda i: (move_options[i][0].gain if move_options[i] else 0.0),
        reverse=True,
    )
    remaining_upside = [0.0] * (len(order) + 1)
    for k in range(len(order) - 1, -1, -1):
        top_gain = move_options[order[k]][0].gain if move_options[order[k]] else 0.0
        remaining_upside[k] = remaining_upside[k + 1] + top_gain

    best_gain = 0.0
    best_combo: list[TransferMove] = []
    used_buy: set[str] = set()
    chosen: list[TransferMove] = []

    def dfs(k: int, cur_gain: float, cur_cost: int) -> None:
        nonlocal best_gain, best_combo
        if cur_gain > best_gain:
            best_gain, best_combo = cur_gain, list(chosen)
        if k == len(order) or cur_gain + remaining_upside[k] <= best_gain:
            return
        i = order[k]
        dfs(k + 1, cur_gain, cur_cost)  # skip this slot
        if len(chosen) >= max_transfers:
            return
        for move in move_options[i]:
            if move.buy_id in used_buy:
                continue
            new_cost = cur_cost + move.net_cost
            if not allow_overdraft and new_cost > budget:
                continue
            if max_club is not None:
                club_counts[move.sell_team] -= 1
                club_counts[move.buy_team] += 1
                fits_club_cap = club_counts[move.buy_team] <= max_club
                if not fits_club_cap:
                    club_counts[move.sell_team] += 1
                    club_counts[move.buy_team] -= 1
                    continue
            used_buy.add(move.buy_id)
            chosen.append(move)
            dfs(k + 1, cur_gain + move.gain, new_cost)
            chosen.pop()
            used_buy.discard(move.buy_id)
            if max_club is not None:
                club_counts[move.sell_team] += 1
                club_counts[move.buy_team] -= 1

    dfs(0, 0.0, 0)

    best_combo.sort(key=lambda m: m.gain, reverse=True)
    net_cost = sum(m.net_cost for m in best_combo)
    budget_after = budget - net_cost
    return TransferPlan(
        horizon=horizon,
        moves=best_combo,
        total_gain=round(best_gain, 2),
        net_cost=net_cost,
        budget_before=budget,
        budget_after=budget_after,
        overdraft=max(0, -budget_after),
    )
