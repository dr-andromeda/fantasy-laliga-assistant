from __future__ import annotations

import pytest

from fantasy_assistant.squad_init import parse_quick_list, render_squad_yaml, resolve_quick_list
from fantasy_assistant.squad_io import SquadResolutionError, load_squad


def test_parse_quick_list_reads_markers_and_ignores_noise() -> None:
    text = """
    # a comment line and a blank line above

    Catalan
    Ferreras (bench)
    Abad *
    """
    entries = parse_quick_list(text)
    assert entries == [
        ("Catalan", False, False),
        ("Ferreras", False, True),
        ("Abad", True, False),
    ]


def test_parse_quick_list_strips_inline_comments() -> None:
    entries = parse_quick_list("Catalan  # our keeper\n")
    assert entries == [("Catalan", False, False)]


def test_resolve_quick_list_builds_a_squad(universe: list) -> None:  # type: ignore[type-arg]
    entries = [("Catalan", False, False), ("Abad", True, False), ("Ferreras", False, True)]
    squad = resolve_quick_list(entries, universe)

    by_name = {sp.player.name: sp for sp in squad.players}
    assert by_name["Catalan"].in_lineup
    assert by_name["Abad"].is_captain
    assert not by_name["Ferreras"].in_lineup


def test_resolve_quick_list_reports_every_bad_name_at_once(universe: list) -> None:  # type: ignore[type-arg]
    entries = [("Totally Fake Player", False, False), ("Also Not Real", False, False)]
    with pytest.raises(SquadResolutionError) as exc_info:
        resolve_quick_list(entries, universe)
    message = str(exc_info.value)
    assert "Totally Fake Player" in message
    assert "Also Not Real" in message


def test_render_squad_yaml_round_trips_through_load_squad(
    tmp_path, universe: list  # type: ignore[type-arg,no-untyped-def]
) -> None:
    entries = [("Catalan", False, False), ("Abad", True, False), ("Ferreras", False, True)]
    squad = resolve_quick_list(entries, universe)

    yaml_text = render_squad_yaml(squad, budget_remaining=2_500_000)
    squad_file = tmp_path / "squad.yaml"
    squad_file.write_text(yaml_text, encoding="utf-8")

    reloaded = load_squad(squad_file, universe)
    names = {sp.player.name for sp in reloaded.players}
    assert names == {"Catalan", "Abad", "Ferreras"}
    assert reloaded.budget_remaining == 2_500_000
    captain = next(sp for sp in reloaded.players if sp.player.name == "Abad")
    assert captain.is_captain
    bench = next(sp for sp in reloaded.players if sp.player.name == "Ferreras")
    assert not bench.in_lineup
