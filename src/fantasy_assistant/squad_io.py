"""Load a hand-written ``squad.yaml`` and resolve its player names to the universe.

Format::

    budget_remaining: 3_400_000
    players:
      - Lewandowski
      - name: Vini            # or a mapping, to pin extra info
        team: Real Madrid
        captain: true
      - "Carvajal (bench)"    # a "(bench)" suffix drops the player from the XI
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz, process

from fantasy_assistant.model import Player, Squad, SquadPlayer

_BENCH_RE = re.compile(r"\s*\((bench|banquillo|sub)\)\s*$", re.IGNORECASE)


def _fold(text: str) -> str:
    """Lower-case and strip accents, so 'cubarsi' matches 'Cubarsí'."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


class SquadResolutionError(ValueError):
    """Raised when a squad entry cannot be matched to exactly one player."""


def _entry_fields(entry: Any) -> tuple[str, str | None, bool, bool]:
    """Normalise one YAML list item to (name, team_hint, is_captain, on_bench)."""
    if isinstance(entry, str):
        raw = entry
        team_hint, captain = None, False
    elif isinstance(entry, dict):
        raw = str(entry.get("name") or entry.get("player") or "")
        team_hint = entry.get("team")
        captain = bool(entry.get("captain") or entry.get("is_captain"))
    else:
        raise SquadResolutionError(f"cannot read squad entry: {entry!r}")

    on_bench = bool(_BENCH_RE.search(raw))
    name = _BENCH_RE.sub("", raw).strip()
    if not name:
        raise SquadResolutionError(f"empty player name in entry {entry!r}")
    return name, team_hint, captain, on_bench


def match_player(
    query: str,
    universe: list[Player],
    *,
    team_hint: str | None = None,
    threshold: int = 78,
) -> Player:
    """Fuzzy-match ``query`` to one player, optionally disambiguated by club."""
    pool = universe
    if team_hint:
        hint = _fold(team_hint)
        by_team = [p for p in universe if fuzz.partial_ratio(hint, _fold(p.team)) > 80]
        if by_team:
            pool = by_team

    choices = {i: _fold(p.name) for i, p in enumerate(pool)}
    results = process.extract(_fold(query), choices, scorer=fuzz.WRatio, limit=3)
    if not results or results[0][1] < threshold:
        near = (
            ", ".join(f"{pool[key].name} ({score:.0f})" for _, score, key in results)
            or "nothing close"
        )
        raise SquadResolutionError(f"no confident match for {query!r}; nearest: {near}")

    _, best_score, best_key = results[0]
    if len(results) > 1 and best_score - results[1][1] < 6:
        tie = ", ".join(pool[key].name for _, _, key in results[:3])
        raise SquadResolutionError(
            f"{query!r} is ambiguous between: {tie} — add a 'team:' hint to disambiguate"
        )
    return pool[best_key]


def load_squad(path: Path | str, universe: list[Player]) -> Squad:
    """Parse ``path`` and return a resolved :class:`Squad`."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = data.get("players") or []
    if not entries:
        raise SquadResolutionError("squad file has no 'players'")

    squad_players: list[SquadPlayer] = []
    for entry in entries:
        name, team_hint, captain, on_bench = _entry_fields(entry)
        player = match_player(name, universe, team_hint=team_hint)
        squad_players.append(
            SquadPlayer(player=player, in_lineup=not on_bench, is_captain=captain)
        )

    return Squad(
        players=squad_players,
        budget_remaining=int(data.get("budget_remaining") or 0),
    )
