"""``fla`` — the command-line entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from fantasy_assistant.backtest import run_backtest
from fantasy_assistant.model import Position, Squad
from fantasy_assistant.optimize import best_lineup
from fantasy_assistant.prediction import PointsPredictor, PredictorConfig, SquadProjection
from fantasy_assistant.providers import AVAILABLE, get_provider
from fantasy_assistant.qubo_squad import compare_solvers
from fantasy_assistant.squad_io import SquadResolutionError, load_squad, match_player
from fantasy_assistant.transfers import suggest_transfers
from fantasy_assistant.valuation import SquadValuation, value_squad

app = typer.Typer(
    add_completion=False,
    help="Decision-support for LaLiga Fantasy: value your squad, spot bargains, "
    "and get transfer and lineup recommendations, backed by a backtest.",
)
players_app = typer.Typer(help="Explore the player universe.")
squad_app = typer.Typer(help="Work with your own squad.")
app.add_typer(players_app, name="players")
app.add_typer(squad_app, name="squad")

console = Console()

ProviderOpt = Annotated[
    str, typer.Option("--provider", "-p", help=f"Fantasy platform: {', '.join(AVAILABLE)}")
]
SourceOpt = Annotated[str, typer.Option("--source", help="Data source: auto | api | csv")]
HorizonOpt = Annotated[int, typer.Option("--horizon", help="Gameweeks to project ahead")]


def _euros(value: float) -> str:
    return f"{value / 1_000_000:.2f}M"


@players_app.command("list")
def players_list(
    provider: ProviderOpt = "laliga",
    source: SourceOpt = "auto",
    position: Annotated[Position | None, typer.Option("--position", case_sensitive=False)] = None,
    team: Annotated[str | None, typer.Option("--team")] = None,
    sort: Annotated[str, typer.Option("--sort", help="points | form | value | price")] = "points",
    top: Annotated[int, typer.Option("--top", "-n")] = 20,
) -> None:
    """List players, filtered and sorted."""
    prov = get_provider(provider, source=source)
    players = prov.load_players()

    if position is not None:
        players = [p for p in players if p.position is position]
    if team is not None:
        players = [p for p in players if team.lower() in p.team.lower()]

    key = {
        "points": lambda p: p.total_points,
        "form": lambda p: p.form(),
        "value": lambda p: p.points_per_million(),
        "price": lambda p: p.price,
    }.get(sort)
    if key is None:
        raise typer.BadParameter("sort must be one of: points, form, value, price")
    players.sort(key=key, reverse=True)

    table = Table(title=f"{provider} — {len(players)} players (top {top} by {sort})")
    for col in ("Player", "Team", "Pos", "Price", "Pts", "Form", "Pts/M€", "Status"):
        table.add_column(col, justify="right" if col not in ("Player", "Team") else "left")
    for p in players[:top]:
        table.add_row(
            p.name, p.team, p.position.value, _euros(p.price), str(p.total_points),
            f"{p.form():.1f}", f"{p.points_per_million():.1f}", p.status.value,
        )
    console.print(table)


@squad_app.command("show")
def squad_show(
    squad_file: Annotated[Path, typer.Option("--squad", "-s", help="Path to squad.yaml")],
    provider: ProviderOpt = "laliga",
    source: SourceOpt = "auto",
    horizon: HorizonOpt = 3,
) -> None:
    """Resolve a hand-written squad file, value it, and project its points."""
    prov = get_provider(provider, source=source)
    universe = prov.load_players()
    fixtures = prov.fixtures(upcoming=max(horizon, 5))
    constraints = prov.constraints()

    try:
        squad = load_squad(squad_file, universe)
    except (SquadResolutionError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    valuation = value_squad(squad, fixtures, violations=squad.validate_against(constraints))
    predictor = PointsPredictor(universe, fixtures, PredictorConfig.load())
    projection = predictor.predict_squad(squad, horizon)
    _print_squad(valuation, squad, projection)


def _print_squad(v: SquadValuation, squad: Squad, proj: SquadProjection) -> None:
    proj_by_id = {p.player_id: p for p in proj.per_player}
    sp_by_id = {sp.player.id: sp for sp in squad.players}

    table = Table(title="Your squad")
    columns = (
        "Player", "Team", "Pos", "XI", "Price", "Pts", "Form", "Next", f"Proj {proj.horizon}GW",
    )
    for col in columns:
        left = col in ("Player", "Team", "Next")
        table.add_column(col, justify="left" if left else "right")

    for row in sorted(v.rows, key=lambda r: (r.player.position.value, -r.form)):
        sp = sp_by_id.get(row.player.id)
        xi = "C" if sp and sp.is_captain else ("-" if sp and sp.in_lineup else "bench")
        nxt = (
            f"{'vs' if row.next_is_home else '@'} {row.next_opponent}"
            if row.next_opponent
            else "-"
        )
        pr = proj_by_id[row.player.id]
        table.add_row(
            row.player.name, row.player.team, row.player.position.value, xi,
            _euros(row.player.price), str(row.player.total_points), f"{row.form:.1f}", nxt,
            f"{pr.expected:.1f}",
        )
    console.print(table)

    console.print(
        f"\nSquad value [b]{_euros(v.total_value)}[/b]  ·  "
        f"budget [b]{_euros(v.budget_remaining)}[/b]  ·  "
        f"bankroll [b]{_euros(v.bankroll)}[/b]  ·  "
        f"season points [b]{v.season_points}[/b]"
    )
    console.print(
        f"Projected XI points, next {proj.horizon} GW: "
        f"[b]{proj.lineup_expected:.1f}[/b] "
        f"(band {proj.lineup_low:.1f}-{proj.lineup_high:.1f}), "
        f"captain worth +{proj.captain_bonus:.1f}"
    )

    top = max(proj.per_player, key=lambda p: p.expected)
    console.print(f"\nTop projection — {top.explain().splitlines()[0]}")

    if v.is_legal:
        console.print("[green]squad is valid[/green]")
    else:
        console.print("[yellow]rule issues:[/yellow]")
        for problem in v.violations:
            console.print(f"  - {problem}")


@squad_app.command("lineup")
def squad_lineup(
    squad_file: Annotated[Path, typer.Option("--squad", "-s", help="Path to squad.yaml")],
    provider: ProviderOpt = "laliga",
    source: SourceOpt = "auto",
    horizon: HorizonOpt = 1,
) -> None:
    """Recommend the best legal XI and captain from the players you own."""
    prov = get_provider(provider, source=source)
    universe = prov.load_players()
    fixtures = prov.fixtures(upcoming=max(horizon, 5))
    constraints = prov.constraints()

    try:
        squad = load_squad(squad_file, universe)
    except (SquadResolutionError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    projection = PointsPredictor(universe, fixtures, PredictorConfig.load()).predict_squad(
        squad, horizon
    )
    plan = best_lineup(squad, projection, constraints)

    exp = {p.player_id: p.expected for p in projection.per_player}
    name = {p.player_id: p.player_name for p in projection.per_player}
    pos = {sp.player.id: sp.player.position.value for sp in squad.players}
    started = {sp.player.id for sp in squad.players if sp.in_lineup}

    table = Table(title=f"Recommended XI  ({plan.formation_str()},  {horizon} GW)")
    for col in ("Pos", "Player", f"Proj {horizon}GW", "Role", "Change"):
        table.add_column(col, justify="left" if col == "Player" else "right")
    for pid in plan.starters:
        role = "[b]C[/b]" if pid == plan.captain_id else "-"
        change = "" if pid in started else "[green]IN[/green]"
        table.add_row(pos[pid], name[pid], f"{exp[pid]:.1f}", role, change)
    console.print(table)

    console.print("\nBench (first sub first):")
    for pid in plan.bench:
        out = "[yellow]OUT[/yellow]" if pid in started else ""
        console.print(f"  {pos[pid]:<3} {name[pid]:<22} {exp[pid]:.1f}  {out}")

    console.print(
        f"\nCaptain: [b]{plan.captain_name}[/b]  ·  "
        f"projected XI (captain x2): [b]{plan.projected_points:.1f}[/b]"
    )
    if plan.improvement is not None:
        sign = "+" if plan.improvement >= 0 else ""
        console.print(
            f"Your current XI projects {plan.current_points:.1f}  ->  "
            f"[b]{sign}{plan.improvement:.1f}[/b] from these changes"
        )


@squad_app.command("transfers")
def squad_transfers(
    squad_file: Annotated[Path, typer.Option("--squad", "-s", help="Path to squad.yaml")],
    provider: ProviderOpt = "laliga",
    source: SourceOpt = "auto",
    horizon: HorizonOpt = 3,
    transfers: Annotated[
        int, typer.Option("--transfers", "-t", help="Max transfers to suggest")
    ] = 3,
    candidates: Annotated[
        int, typer.Option("--candidates", help="Replacement candidates considered per player")
    ] = 5,
    allow_overdraft: Annotated[
        bool,
        typer.Option(
            "--allow-overdraft",
            help="Allow a plan that leaves your balance negative (you must clear it "
            "yourself before the next deadline, or you score 0 points that gameweek)",
        ),
    ] = False,
) -> None:
    """Suggest same-position transfers that raise your projected points."""
    prov = get_provider(provider, source=source)
    universe = prov.load_players()
    fixtures = prov.fixtures(upcoming=max(horizon, 5))
    constraints = prov.constraints()

    try:
        squad = load_squad(squad_file, universe)
    except (SquadResolutionError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    predictor = PointsPredictor(universe, fixtures, PredictorConfig.load())
    plan = suggest_transfers(
        squad, universe, predictor, constraints,
        horizon=horizon, max_transfers=transfers, candidates_per_slot=candidates,
        allow_overdraft=allow_overdraft,
    )

    if not plan.moves:
        console.print(f"[yellow]No transfer over {horizon} GW beats keeping your squad.[/yellow]")
        return

    table = Table(title=f"Suggested transfers  ({horizon} GW, +{plan.total_gain:.1f} pts)")
    for col in ("Pos", "Out", "In", "Net cost", "Gain"):
        table.add_column(col, justify="left" if col in ("Pos", "Out", "In") else "right")
    for m in plan.moves:
        table.add_row(
            m.position.value,
            f"{m.sell_name} ({m.sell_team})",
            f"{m.buy_name} ({m.buy_team})",
            f"{m.net_cost / 1_000_000:+.2f}M",
            f"+{m.gain:.1f}",
        )
    console.print(table)

    console.print(
        f"\nNet spend [b]{plan.net_cost / 1_000_000:+.2f}M[/b]  ·  "
        f"budget {plan.budget_before / 1_000_000:.2f}M -> {plan.budget_after / 1_000_000:.2f}M"
    )
    if plan.overdraft:
        console.print(
            f"[red]WARNING: ends {plan.overdraft / 1_000_000:.2f}M short. Clear it "
            "(sell/rescind) before the next deadline or you score ZERO points that "
            "gameweek.[/red]"
        )


@squad_app.command("backtest")
def squad_backtest(
    squad_file: Annotated[Path, typer.Option("--squad", "-s", help="Path to squad.yaml")],
    provider: ProviderOpt = "laliga",
    source: SourceOpt = "auto",
    half_life: Annotated[
        float | None,
        typer.Option("--half-life", help="Form half-life in GW (default: prediction.yaml)"),
    ] = None,
    min_history: Annotated[
        int, typer.Option("--min-history", help="Gameweeks of history before the first prediction")
    ] = 2,
) -> None:
    """Walk-forward backtest of the lineup/captain pick vs. static and hindsight.

    Only replays the trailing gameweek history each player already carries -- see
    `fantasy_assistant.backtest` for exactly what is (and isn't) evaluated.
    """
    prov = get_provider(provider, source=source)
    universe = prov.load_players()
    constraints = prov.constraints()

    try:
        squad = load_squad(squad_file, universe)
    except (SquadResolutionError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    hl = half_life if half_life is not None else PredictorConfig.load().form_half_life
    try:
        report = run_backtest(
            squad, constraints, form_half_life=hl, min_history=min_history
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    table = Table(title=f"Backtest — {len(report.weeks)} gameweeks")
    columns = ("GW", "Recommended", "Static", "Hindsight", "Captain (rec.)", "Captain (hindsight)")
    for col in columns:
        table.add_column(col, justify="left" if "Captain" in col else "right")
    for w in report.weeks:
        table.add_row(
            str(w.gameweek_index), f"{w.recommended_points:.1f}", f"{w.static_points:.1f}",
            f"{w.hindsight_points:.1f}", w.recommended_captain, w.hindsight_captain,
        )
    console.print(table)

    console.print(
        f"\nTotals — recommended [b]{report.recommended_total:.1f}[/b]  ·  "
        f"static {report.static_total:.1f}  ·  hindsight {report.hindsight_total:.1f}"
    )
    console.print(f"Edge over doing nothing: [b]{report.edge_over_static:+.1f}[/b] pts")
    if report.capture_rate is not None:
        console.print(
            f"Captured [b]{report.capture_rate * 100:.0f}%[/b] of the hindsight-optimal points"
        )


@squad_app.command("qubo-transfers")
def squad_qubo_transfers(
    squad_file: Annotated[Path, typer.Option("--squad", "-s", help="Path to squad.yaml")],
    provider: ProviderOpt = "laliga",
    source: SourceOpt = "auto",
    horizon: HorizonOpt = 3,
    candidates: Annotated[
        int,
        typer.Option("--candidates", help="Unowned candidates per position in the solver's pool"),
    ] = 3,
    seed: Annotated[int, typer.Option("--seed", help="RNG seed, for reproducible runs")] = 7,
) -> None:
    """Cross-position squad restructuring via QUBO: exact vs. simulated annealing vs. tabu.

    Unlike `squad transfers` (same-position swaps only), this picks the best legal,
    affordable squad from a pool spanning every position at once -- solved with
    qubo-forge (`pip install -e ".[solver]"`). See `fantasy_assistant.qubo_squad`
    for the formulation and its honesty caveats about solver scale.
    """
    prov = get_provider(provider, source=source)
    universe = prov.load_players()
    fixtures = prov.fixtures(upcoming=max(horizon, 5))
    constraints = prov.constraints()

    try:
        squad = load_squad(squad_file, universe)
    except (SquadResolutionError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    predictor = PointsPredictor(universe, fixtures, PredictorConfig.load())
    expected = {p.id: predictor.predict(p, horizon).expected for p in universe}

    try:
        report = compare_solvers(
            squad, universe, expected, constraints,
            candidates_per_position=candidates, seed=seed,
        )
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    names = {p.id: p.name for p in universe}
    owned_ids = {p.id for p in squad.owned}

    table = Table(title=f"Solver comparison — {report.pool_size}-variable pool")
    for col in ("Solver", "Feasible", "Pts", "Cost", "Energy", "Time (s)"):
        table.add_column(col, justify="left" if col in ("Solver", "Feasible") else "right")
    for sel in (report.exhaustive, report.simulated_annealing, report.tabu):
        if sel is None:
            table.add_row("exhaustive", "[dim]skipped[/dim]", "-", "-", "-", "-")
            continue
        feasible = "[green]yes[/green]" if sel.feasible else "[red]no[/red]"
        table.add_row(
            sel.solver, feasible, f"{sel.total_expected:.1f}",
            f"{sel.total_cost / 1_000_000:.2f}M", f"{sel.energy:.1f}", f"{sel.solve_seconds:.3f}",
        )
    console.print(table)
    console.print(f"\nBankroll: [b]{report.bankroll / 1_000_000:.2f}M[/b]")
    console.print(report.note)

    for sel in (report.exhaustive, report.simulated_annealing, report.tabu):
        if sel is None:
            continue
        if not sel.feasible:
            console.print(f"\n[red]{sel.solver}: infeasible -- {'; '.join(sel.violations)}[/red]")
            continue
        chosen = set(sel.player_ids)
        into = sorted(names.get(pid, pid) for pid in chosen - owned_ids)
        out = sorted(names.get(pid, pid) for pid in owned_ids - chosen if pid in names)
        console.print(f"\n[b]{sel.solver}[/b] vs. your current squad:")
        console.print(f"  IN:  {', '.join(into) if into else '(no change)'}")
        console.print(f"  OUT: {', '.join(out) if out else '(no change)'}")


@app.command()
def predict(
    name: Annotated[str, typer.Argument(help="Player name (fuzzy-matched)")],
    provider: ProviderOpt = "laliga",
    source: SourceOpt = "auto",
    horizon: HorizonOpt = 5,
    team: Annotated[str | None, typer.Option("--team", help="Club hint to disambiguate")] = None,
) -> None:
    """Show the points projection for one player, with the reasoning."""
    prov = get_provider(provider, source=source)
    universe = prov.load_players()
    fixtures = prov.fixtures(upcoming=max(horizon, 5))

    try:
        player = match_player(name, universe, team_hint=team)
    except SquadResolutionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    predictor = PointsPredictor(universe, fixtures, PredictorConfig.load())
    console.print(predictor.predict(player, horizon).explain())


@app.command()
def providers() -> None:
    """List the fantasy platforms this build supports."""
    for key in AVAILABLE:
        console.print(f"- {key}")


if __name__ == "__main__":
    app()
