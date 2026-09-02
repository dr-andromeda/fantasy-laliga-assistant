from __future__ import annotations

from pathlib import Path

import pytest

from fantasy_assistant.model import Player
from fantasy_assistant.squad_io import SquadResolutionError, load_squad, match_player

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "squad.example.yaml"


def test_loads_the_example_squad(universe: list[Player]) -> None:
    squad = load_squad(EXAMPLE, universe)
    assert len(squad.players) == 15
    assert squad.budget_remaining == 2_600_000

    names = {sp.player.name for sp in squad.players}
    assert "Bellingham" in names
    assert "Vinícius Jr" in names          # matched from "Vinicius"
    assert "Giménez" in names              # matched from "Gimenez"

    captains = [sp for sp in squad.players if sp.is_captain]
    assert len(captains) == 1 and captains[0].player.name == "Bellingham"

    benched = {sp.player.name for sp in squad.players if not sp.in_lineup}
    assert benched == {"Unai Simón", "Vivian", "Zubimendi", "Griezmann"}
    assert sum(sp.in_lineup for sp in squad.players) == 11


def test_fuzzy_match_tolerates_accents_and_short_forms(universe: list[Player]) -> None:
    assert match_player("Lewandowski", universe).name == "Lewandowski"
    assert match_player("cubarsi", universe).name == "Cubarsí"
    assert match_player("Isco", universe, team_hint="Betis").name == "Isco"


def test_unknown_player_raises(universe: list[Player]) -> None:
    with pytest.raises(SquadResolutionError, match="no confident match"):
        match_player("Cristiano Ronaldo", universe)


def test_missing_players_key_raises(tmp_path: Path, universe: list[Player]) -> None:
    bad = tmp_path / "squad.yaml"
    bad.write_text("budget_remaining: 1000\n", encoding="utf-8")
    with pytest.raises(SquadResolutionError, match="no 'players'"):
        load_squad(bad, universe)
