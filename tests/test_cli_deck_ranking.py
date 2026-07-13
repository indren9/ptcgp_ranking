from pathlib import Path
import sys

from cli.deck_ranking import main


class DummyResult:
    outputs = {"ranking": Path("ranking.csv")}
    frames = {"mars_ranking": object()}


def test_cli_run_passes_options_to_pipeline(monkeypatch, tmp_path):
    calls = []

    def fake_run_deck_ranking(**kwargs):
        calls.append(kwargs)
        return DummyResult()

    monkeypatch.setattr("cli.deck_ranking.run_deck_ranking", fake_run_deck_ranking)

    code = main(
        [
            "run",
            "--base-dir",
            str(tmp_path),
            "--config",
            "config/test.yaml",
            "--skip-scrape",
            "--skip-core",
            "--skip-heatmap",
            "--progress",
            "--heatmap-top-n",
            "15",
        ]
    )

    assert code == 0
    assert calls == [
        {
            "base_dir": tmp_path.resolve(),
            "config_path": "config/test.yaml",
            "run_scrape": False,
            "run_core": False,
            "run_mars": True,
            "run_heatmap": False,
            "run_report": True,
            "heatmap_top_n": 15,
            "show_progress": True,
        }
    ]


def test_cli_defaults_to_run_command(monkeypatch, tmp_path):
    calls = []

    def fake_run_deck_ranking(**kwargs):
        calls.append(kwargs)
        return DummyResult()

    monkeypatch.setattr("cli.deck_ranking.run_deck_ranking", fake_run_deck_ranking)

    code = main(["--base-dir", str(tmp_path), "--skip-mars", "--skip-report"])

    assert code == 0
    assert calls[0]["run_scrape"] is True
    assert calls[0]["run_core"] is None
    assert calls[0]["run_mars"] is False
    assert calls[0]["run_heatmap"] is True
    assert calls[0]["run_report"] is False
    assert calls[0]["show_progress"] is False


def test_cli_main_uses_process_argv_when_no_explicit_argv(monkeypatch, tmp_path):
    calls = []

    def fake_run_deck_ranking(**kwargs):
        calls.append(kwargs)
        return DummyResult()

    monkeypatch.setattr("cli.deck_ranking.run_deck_ranking", fake_run_deck_ranking)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "python -m cli.deck_ranking",
            "run",
            "--base-dir",
            str(tmp_path),
            "--config",
            "config/config_tcg.yaml",
            "--skip-scrape",
        ],
    )

    code = main()

    assert code == 0
    assert calls[0]["base_dir"] == tmp_path.resolve()
    assert calls[0]["config_path"] == "config/config_tcg.yaml"
    assert calls[0]["run_scrape"] is False


def test_cli_returns_error_code_on_failure(monkeypatch):
    def fake_run_deck_ranking(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("cli.deck_ranking.run_deck_ranking", fake_run_deck_ranking)

    assert main(["run"]) == 1
