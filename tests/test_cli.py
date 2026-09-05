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
    assert "Cifre" in result.output


def test_players_list_rejects_bad_sort() -> None:
    result = runner.invoke(app, ["players", "list", "--source", "csv", "--sort", "nonsense"])
    assert result.exit_code != 0


def test_squad_show_runs_on_example() -> None:
    result = runner.invoke(
        app, ["squad", "show", "--source", "csv", "--squad", str(EXAMPLE), "--horizon", "4"]
    )
    assert result.exit_code == 0, result.output
    assert "Your squad" in result.output
    assert "Abad" in result.output
    assert "squad is valid" in result.output
    assert "Projected XI points, next 4 GW" in result.output


def test_predict_command_explains_a_player() -> None:
    result = runner.invoke(
        app, ["predict", "Abad", "--source", "csv", "--horizon", "3"]
    )
    assert result.exit_code == 0, result.output
    assert "Abad:" in result.output
    assert "form rate" in result.output


def test_predict_command_rejects_unknown_player() -> None:
    result = runner.invoke(app, ["predict", "Zlatan Ibrahimovic", "--source", "csv"])
    assert result.exit_code == 1


def test_squad_lineup_recommends_an_xi() -> None:
    result = runner.invoke(
        app, ["squad", "lineup", "--source", "csv", "--squad", str(EXAMPLE), "--horizon", "2"]
    )
    assert result.exit_code == 0, result.output
    assert "Recommended XI" in result.output
    assert "Captain:" in result.output
    assert "Bench (first sub first):" in result.output


def test_squad_show_reports_missing_file() -> None:
    result = runner.invoke(app, ["squad", "show", "--source", "csv", "--squad", "nope.yaml"])
    assert result.exit_code == 1


def test_providers_command() -> None:
    result = runner.invoke(app, ["providers"])
    assert result.exit_code == 0
    assert "laliga" in result.output


def test_squad_init_writes_a_resolved_squad_yaml(tmp_path: Path) -> None:
    quick_list = tmp_path / "my_team.txt"
    quick_list.write_text("Catalan\nAbad *\nFerreras (bench)\n", encoding="utf-8")
    out_file = tmp_path / "squad.yaml"

    result = runner.invoke(
        app, ["squad", "init", "--source", "csv", "--from", str(quick_list), "--out", str(out_file)]
    )

    assert result.exit_code == 0, result.output
    assert "Wrote 3 players" in result.output
    text = out_file.read_text(encoding="utf-8")
    assert "Catalan" in text
    assert "captain: true" in text
    assert "Ferreras (bench)" in text


def test_squad_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    quick_list = tmp_path / "my_team.txt"
    quick_list.write_text("Catalan\n", encoding="utf-8")
    out_file = tmp_path / "squad.yaml"
    out_file.write_text("existing content", encoding="utf-8")

    result = runner.invoke(
        app, ["squad", "init", "--source", "csv", "--from", str(quick_list), "--out", str(out_file)]
    )

    assert result.exit_code == 1
    assert out_file.read_text(encoding="utf-8") == "existing content"


def test_squad_init_reports_unresolved_names(tmp_path: Path) -> None:
    quick_list = tmp_path / "my_team.txt"
    quick_list.write_text("Totally Fake Player\n", encoding="utf-8")
    out_file = tmp_path / "squad.yaml"

    result = runner.invoke(
        app, ["squad", "init", "--source", "csv", "--from", str(quick_list), "--out", str(out_file)]
    )

    assert result.exit_code == 1
    assert "Totally Fake Player" in result.output
    assert not out_file.exists()
