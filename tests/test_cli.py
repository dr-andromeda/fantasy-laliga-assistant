from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from fantasy_assistant.cli import app

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "squad.example.yaml"


def test_players_list_runs() -> None:
    result = runner.invoke(
        app, ["players", "list", "--source", "csv", "--position", "FWD", "-n", "5"]
    )
    assert result.exit_code == 0, result.output
    assert "Lewandowski" in result.output


def test_players_list_rejects_bad_sort() -> None:
    result = runner.invoke(app, ["players", "list", "--source", "csv", "--sort", "nonsense"])
    assert result.exit_code != 0


def test_squad_show_runs_on_example() -> None:
    result = runner.invoke(app, ["squad", "show", "--source", "csv", "--squad", str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert "Your squad" in result.output
    assert "Bellingham" in result.output
    assert "squad is valid" in result.output


def test_squad_show_reports_missing_file() -> None:
    result = runner.invoke(app, ["squad", "show", "--source", "csv", "--squad", "nope.yaml"])
    assert result.exit_code == 1


def test_providers_command() -> None:
    result = runner.invoke(app, ["providers"])
    assert result.exit_code == 0
    assert "laliga" in result.output
