from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from domain.expansions import Expansion
from scripts.export_expansions_catalog import catalog_rows, main, write_catalog_csv


def test_write_catalog_csv_is_google_sheets_ready(tmp_path: Path):
    output = tmp_path / "public" / "expansions.csv"

    count = write_catalog_csv(
        output,
        [
            Expansion(code="B4", name="Ruler of the Skies", is_current=True),
            Expansion(code="A1", name="Genetic Apex"),
        ],
    )

    assert count == 2
    with output.open(newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [
            ["code", "name"],
            ["B4", "Ruler of the Skies"],
            ["A1", "Genetic Apex"],
        ]


def test_catalog_rows_rejects_empty_or_duplicate_catalogs():
    with pytest.raises(ValueError, match="empty"):
        catalog_rows([])
    with pytest.raises(ValueError, match="Duplicate"):
        catalog_rows(
            [
                Expansion(code="A1", name="Genetic Apex"),
                Expansion(code="A1", name="Duplicate"),
            ]
        )


def test_main_can_export_existing_json_cache(tmp_path: Path):
    cache = tmp_path / "expansions.json"
    output = tmp_path / "expansions.csv"
    cache.write_text(
        json.dumps(
            {
                "fetched_at": "2026-08-14T07:16:51",
                "expansions": [
                    {"code": "B4", "name": "B4 - Ruler of the Skies", "is_current": True, "rotation": None}
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(["--cache-json", str(cache), "--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == "code,name\nB4,Ruler of the Skies\n"
