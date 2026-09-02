"""``fla`` — the command-line entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from fantasy_assistant.model import Position
from fantasy_assistant.providers import AVAILABLE, get_provider
from fantasy_assistant.squad_io import SquadResolutionError, load_squad
from fantasy_assistant.valuation import SquadValuation, value_squad

app = typer.Typer(
    add_completion=False,
    help="Decision-support for LaLiga Fantasy: value your squad, spot bargains, "
    "and (soon) get transfer and lineup recommendations.",
)
players_app = typer.Typer(help="Explore the player universe.")
squad_app = typer.Typer(help="Work with your own squad.")
app.add_typer(players_app, name="players")
app.add_typer(squad_app, name="squad")

console = Console()

ProviderOpt = Annotated[
    str, typer.Option("--provider", "-p", help=f"Fantasy platform: {', '.join(AVAILABLE)}")
]
SourceOpt = Annotated[
    str, typer.Option("--source", help="Data source: auto | api | csv")
]


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
) -> None:
    """Resolve a hand-written squad file and print it valued."""
    prov = get_provider(provider, source=source)
    universe = prov.load_players()
    fixtures = prov.fixtures()
    constraints = prov.constraints()

    try:
        squad = load_squad(squad_file, universe)
    except (SquadResolutionError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    valuation = value_squad(squad, fixtures, violations=squad.validate_against(constraints))
    _print_squad(valuation, squad)


def _print_squad(v: SquadValuation, squad: object) -> None:
    table = Table(title="Your squad")
    for col in ("Player", "Team", "Pos", "XI", "Price", "Pts", "Form", "Next"):
        table.add_column(col, justify="right" if col not in ("Player", "Team", "Next") else "left")

    owned_captain = {id(sp.player): sp for sp in getattr(squad, "players", [])}
    for row in sorted(v.rows, key=lambda r: (r.player.position.value, -r.form)):
        sp = owned_captain.get(id(row.player))
        xi = "C" if sp and sp.is_captain else ("·" if sp and sp.in_lineup else "bench")
        nxt = (
            f"{'vs' if row.next_is_home else '@'} {row.next_opponent}"
            if row.next_opponent
            else "—"
        )
        table.add_row(
            row.player.name, row.player.team, row.player.position.value, xi,
            _euros(row.player.price), str(row.player.total_points), f"{row.form:.1f}", nxt,
        )
    console.print(table)

    console.print(
        f"\nSquad value [b]{_euros(v.total_value)}[/b]  ·  "
        f"budget [b]{_euros(v.budget_remaining)}[/b]  ·  "
        f"bankroll [b]{_euros(v.bankroll)}[/b]  ·  "
        f"season points [b]{v.season_points}[/b]"
    )
    if v.is_legal:
        console.print("[green]squad is valid[/green]")
    else:
        console.print("[yellow]rule issues:[/yellow]")
        for problem in v.violations:
            console.print(f"  • {problem}")


@app.command()
def providers() -> None:
    """List the fantasy platforms this build supports."""
    for key in AVAILABLE:
        console.print(f"• {key}")


if __name__ == "__main__":
    app()
