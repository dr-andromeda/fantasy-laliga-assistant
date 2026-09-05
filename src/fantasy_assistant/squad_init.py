"""Turn a quick, flat list of player names into a validated ``squad.yaml``.

A real "import my team" needs the platform's private, per-user API, which
means a login token -- something this project deliberately doesn't handle
(see the README's Caveats: only public, unauthenticated data). ``fla squad
init`` is the practical alternative: type or paste a plain list of names --
no YAML syntax, no team hints unless a name is ambiguous -- and get back a
canonical ``squad.yaml``, with typos caught all at once instead of silently
producing the wrong squad.

Input format, one player per line::

    Courtois
    Carvajal
    Bellingham *          # "*" marks the captain
    Vinicius (bench)      # "(bench)" keeps them out of the starting XI
    # blank lines and "#" comments are ignored
"""

from __future__ import annotations

import re

from fantasy_assistant.model import Player, Squad, SquadPlayer
from fantasy_assistant.squad_io import _BENCH_RE, SquadResolutionError, match_player

_CAPTAIN_RE = re.compile(r"\s*\*\s*$")

QuickEntry = tuple[str, bool, bool]  # (name, is_captain, on_bench)


def parse_quick_list(text: str) -> list[QuickEntry]:
    """One player per line -> ``[(name, is_captain, on_bench), ...]``."""
    entries: list[QuickEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        captain = bool(_CAPTAIN_RE.search(line))
        line = _CAPTAIN_RE.sub("", line).strip()
        on_bench = bool(_BENCH_RE.search(line))
        name = _BENCH_RE.sub("", line).strip()
        if name:
            entries.append((name, captain, on_bench))
    return entries


def resolve_quick_list(entries: list[QuickEntry], universe: list[Player]) -> Squad:
    """Match every entry against the universe.

    Collects *every* unresolved name before raising, rather than stopping at
    the first typo -- with ~15 names to type by hand, seeing every mistake at
    once beats a fix-one-rerun-repeat loop.
    """
    resolved: list[SquadPlayer] = []
    errors: list[str] = []
    for name, captain, on_bench in entries:
        try:
            player = match_player(name, universe)
        except SquadResolutionError as exc:
            errors.append(str(exc))
            continue
        resolved.append(SquadPlayer(player=player, in_lineup=not on_bench, is_captain=captain))
    if errors:
        raise SquadResolutionError("\n".join(errors))
    return Squad(players=resolved)


def render_squad_yaml(squad: Squad, budget_remaining: int) -> str:
    """Render a :class:`Squad` back out as a hand-editable ``squad.yaml``."""
    lines = [f"budget_remaining: {budget_remaining}", "players:"]
    for sp in squad.players:
        suffix = " (bench)" if not sp.in_lineup else ""
        if sp.is_captain:
            lines.append(f"  - name: {sp.player.name}{suffix}")
            lines.append("    captain: true")
        else:
            lines.append(f"  - {sp.player.name}{suffix}")
    return "\n".join(lines) + "\n"
