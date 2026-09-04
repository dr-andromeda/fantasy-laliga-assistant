"""Formulate "assemble the best legal squad within budget" as a QUBO, and solve
it two ways: qubo-forge's exact brute-force solver (small instances only, as
ground truth) and its metaheuristics -- simulated annealing and tabu search --
at a realistic scale.

:mod:`fantasy_assistant.transfers` only considers same-position swaps: the one
kind of move that's legal on its own, so an exact search over a pruned
candidate list is cheap. This module is the harder problem that optimizer
deliberately avoids -- selecting from a *joint* pool spanning every position
at once, so a recommendation can restructure the squad across positions in
one shot. That joint, combinatorial selection is exactly the class of problem
qubo-forge and metaheuristics-jvm exist for, and this is the piece that ties
this "ordinary software" project back to the rest of the portfolio.

Requires the optional ``solver`` dependency group: ``pip install -e ".[solver]"``.

## The formulation

One binary variable per player in a *candidate pool* (the owned squad, plus
the top players by projected points at each position). Selecting a player
"costs" their price against the squad's bankroll (``budget_remaining`` plus
the value of every owned player -- what you'd have if you sold the whole
squad), so keeping an owned player and buying a new one are the same kind of
decision; nothing needs explicit buy/sell bookkeeping::

    maximize   sum_i expected_i * x_i
    subject to sum_i x_i == squad_by_position[pos]   for each position
               sum_i price_i * x_i <= bankroll

Position counts become equality-constraint penalties
(``QuboModel.add_constraint_eq``). The budget inequality becomes an equality
with a binary-decomposed slack variable -- the standard Lucas (2014) trick,
the same one ``qubo_forge.problems.knapsack`` uses -- so it can be penalized
the same way. Prices are rounded to ``budget_unit`` (default 5,000,000 EUR)
before being used as QUBO weights -- coarser than that, and single-spin-flip
search (below) reliably fails to satisfy the budget and position constraints
*together*; see the next section. The reported cost and feasibility check
always use the real, unrounded prices, so this only affects how hard the
solver has to work, never what counts as a legal answer.

## Honesty about scale, and a real finding

The exact solver is brute force -- ``2**n`` -- so it only runs on a pool small
enough to fit (a handful of candidates per position); at realistic pool sizes
(tens of variables) only the metaheuristics are tractable, and there is no
guarantee they find the true optimum -- just a report of what they found, how
long it took, and whether the result actually respects every constraint
(penalty methods can and do return infeasible samples; this is checked and
reported, never assumed).

Empirically, that infeasibility is not rare here. Each constraint alone
(position counts *or* budget) is easy for both solvers -- but combined, they
pull in different directions: the cheapest way to fix a budget overshoot is
to drop a player, which breaks a position count, and vice versa. Simulated
annealing's random-accept single-spin-flip moves rarely escape that trap
within a practical sweep budget; tabu search's greedy least-cost move plus
restarts does much better here (see the module's tests and
``fla squad qubo-transfers``) -- a genuine, reproducible result about which
search strategy suits this constraint shape, not a tuning failure to paper
over. A production system would likely reach for swap-style moves or a
constraint-aware encoding instead of raw penalty terms; this module
deliberately keeps the plain penalty-method formulation so that limitation is
visible rather than engineered away.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from fantasy_assistant.model import Constraints, Player, Position, Squad

try:
    from qubo_forge.model import QuboModel
    from qubo_forge.solvers.exhaustive import ExhaustiveSolver
    from qubo_forge.solvers.simulated_annealing import SimulatedAnnealingSolver
    from qubo_forge.solvers.tabu import TabuSolver
except ImportError as _exc:
    _QUBO_FORGE_ERROR: ImportError | None = _exc
else:
    _QUBO_FORGE_ERROR = None


def _require_qubo_forge() -> None:
    if _QUBO_FORGE_ERROR is not None:
        raise ImportError(
            'this command needs the optional qubo-forge dependency: pip install -e ".[solver]"'
        ) from _QUBO_FORGE_ERROR


class SquadSelection(BaseModel):
    solver: str
    player_ids: list[str]
    total_expected: float
    total_cost: int
    bankroll: int
    energy: float
    solve_seconds: float
    feasible: bool
    violations: list[str] = Field(default_factory=list)


class ComparisonReport(BaseModel):
    pool_size: int
    bankroll: int
    exhaustive: SquadSelection | None
    simulated_annealing: SquadSelection
    tabu: SquadSelection
    note: str


def build_pool(
    squad: Squad,
    universe: list[Player],
    expected: dict[str, float],
    *,
    candidates_per_position: int = 6,
) -> list[Player]:
    """The owned squad plus the top ``candidates_per_position`` unowned players
    by projected points at each position -- the joint pool the QUBO selects from.
    """
    owned_ids = {p.id for p in squad.owned}
    by_position: dict[Position, list[Player]] = {pos: [] for pos in Position}
    for p in universe:
        if p.id not in owned_ids:
            by_position[p.position].append(p)
    for pos in by_position:
        by_position[pos].sort(key=lambda p: expected.get(p.id, 0.0), reverse=True)

    pool = list(squad.owned)
    for players in by_position.values():
        pool.extend(players[:candidates_per_position])
    return pool


def _slack_weights(capacity: int) -> list[int]:
    """Binary weights whose subset sums cover exactly ``0 .. capacity`` (Lucas 2014)."""
    if capacity <= 0:
        return []
    bits = capacity.bit_length()
    weights = [1 << k for k in range(bits - 1)]
    weights.append(capacity - (2 ** (bits - 1) - 1))  # cap the top so max == capacity
    return weights


def build_squad_qubo(
    pool: list[Player],
    expected: dict[str, float],
    constraints: Constraints,
    bankroll: int,
    *,
    budget_unit: int = 5_000_000,
    penalty: float | None = None,
) -> Any:
    """The QUBO for picking the best legal, affordable squad from ``pool``."""
    _require_qubo_forge()

    model = QuboModel()
    for p in pool:
        model.add_variable(p.id)
    for p in pool:
        model.add_linear(p.id, -expected.get(p.id, 0.0))

    scale = max(sum(abs(expected.get(p.id, 0.0)) for p in pool), 1.0)
    pen = penalty if penalty is not None else scale * 4.0

    by_position: dict[Position, list[Player]] = {}
    for p in pool:
        by_position.setdefault(p.position, []).append(p)
    for pos, target in constraints.squad_by_position.items():
        terms: dict[Any, float] = {p.id: 1.0 for p in by_position.get(pos, [])}
        if terms:
            model.add_constraint_eq(terms, target=float(target), penalty=pen)

    capacity = max(bankroll, 0) // budget_unit
    budget_terms: dict[Any, float] = {
        p.id: float(round(p.price / budget_unit)) for p in pool
    }
    for k, weight in enumerate(_slack_weights(capacity)):
        budget_terms[("slack", k)] = float(weight)
    model.add_constraint_eq(budget_terms, target=float(capacity), penalty=pen)

    return model


def _decode(
    model: Any,
    result: Any,
    pool: list[Player],
    expected: dict[str, float],
    constraints: Constraints,
    bankroll: int,
    *,
    solver_name: str,
    solve_seconds: float,
) -> SquadSelection:
    by_id = {p.id: p for p in pool}
    chosen = [str(pid) for pid, bit in result.sample.items() if bit == 1 and pid in by_id]

    total_cost = sum(by_id[pid].price for pid in chosen)
    total_expected = sum(expected.get(pid, 0.0) for pid in chosen)

    violations: list[str] = []
    counts: dict[Position, int] = {}
    for pid in chosen:
        pos = by_id[pid].position
        counts[pos] = counts.get(pos, 0) + 1
    for pos, target in constraints.squad_by_position.items():
        got = counts.get(pos, 0)
        if got != target:
            violations.append(f"{pos.value}: selected {got}, need {target}")
    if len(chosen) != constraints.squad_size:
        violations.append(f"squad has {len(chosen)} players, expected {constraints.squad_size}")
    if total_cost > bankroll:
        over = (total_cost - bankroll) / 1_000_000
        violations.append(f"costs {over:.2f}M more than the {bankroll / 1_000_000:.2f}M bankroll")

    return SquadSelection(
        solver=solver_name,
        player_ids=chosen,
        total_expected=round(total_expected, 2),
        total_cost=total_cost,
        bankroll=bankroll,
        energy=round(float(result.energy), 3),
        solve_seconds=round(solve_seconds, 4),
        feasible=not violations,
        violations=violations,
    )


def _timed_solve(
    model: Any,
    solver: Any,
    pool: list[Player],
    expected: dict[str, float],
    constraints: Constraints,
    bankroll: int,
    *,
    solver_name: str,
) -> SquadSelection:
    t0 = time.perf_counter()
    result = solver.solve(model)
    dt = time.perf_counter() - t0
    return _decode(
        model, result, pool, expected, constraints, bankroll,
        solver_name=solver_name, solve_seconds=dt,
    )


def compare_solvers(
    squad: Squad,
    universe: list[Player],
    expected: dict[str, float],
    constraints: Constraints,
    *,
    candidates_per_position: int = 3,
    exhaustive_candidates_per_position: int = 1,
    seed: int | None = 7,
) -> ComparisonReport:
    """Solve the full squad-selection QUBO with SA and Tabu, plus an exact
    brute-force run on a deliberately small instance to sanity-check the
    formulation.

    The solver hyperparameters below (Tabu's restarts and iteration count
    especially) were picked empirically to reliably reach a feasible squad at
    this problem's typical size -- see the module docstring's "Honesty about
    scale" section for what happens at the defaults qubo-forge ships with.
    """
    _require_qubo_forge()

    bankroll = squad.bankroll()
    pool = build_pool(squad, universe, expected, candidates_per_position=candidates_per_position)
    model = build_squad_qubo(pool, expected, constraints, bankroll)

    sa = _timed_solve(
        model, SimulatedAnnealingSolver(seed=seed, num_reads=40, num_sweeps=2000),
        pool, expected, constraints, bankroll, solver_name="simulated_annealing",
    )
    tabu = _timed_solve(
        model, TabuSolver(seed=seed, num_restarts=20, max_iterations=2000),
        pool, expected, constraints, bankroll, solver_name="tabu",
    )

    small_pool = build_pool(
        squad, universe, expected, candidates_per_position=exhaustive_candidates_per_position
    )
    small_model = build_squad_qubo(small_pool, expected, constraints, bankroll)
    max_exhaustive = ExhaustiveSolver().max_variables
    if small_model.num_variables <= max_exhaustive:
        exhaustive = _timed_solve(
            small_model, ExhaustiveSolver(), small_pool, expected, constraints, bankroll,
            solver_name="exhaustive",
        )
        note = (
            f"Exhaustive ran on a reduced {small_model.num_variables}-variable instance "
            f"({exhaustive_candidates_per_position} candidate(s)/position) as ground truth; "
            f"SA and Tabu ran on the full {model.num_variables}-variable pool."
        )
    else:
        exhaustive = None
        owned_n = len(squad.players)
        note = (
            f"Skipping the exhaustive solver: the {owned_n} owned players alone push even the "
            f"{exhaustive_candidates_per_position}-candidate reduced instance to "
            f"{small_model.num_variables} variables (> {max_exhaustive}). A real {owned_n}-player "
            "squad is inherently too big to brute-force -- exact validation of this formulation "
            "lives in the test suite, on synthetic squads small enough to fit. SA and Tabu below "
            "have no exact baseline to check against."
        )

    return ComparisonReport(
        pool_size=model.num_variables,
        bankroll=bankroll,
        exhaustive=exhaustive,
        simulated_annealing=sa,
        tabu=tabu,
        note=note,
    )
