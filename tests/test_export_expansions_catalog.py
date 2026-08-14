from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from domain.expansions import Expansion
from scripts.export_expansions_catalog import (
    catalog_rows,
    load_manual_catalog,
    main,
    merge_catalogs,
    write_catalog_csv,
)


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
            ["A1", "Genetic Apex"],
            ["B4", "Ruler of the Skies"],
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


def test_manual_catalog_is_merged_overrides_duplicates_and_sorts_naturally(tmp_path: Path):
    manual = tmp_path / "manual.csv"
    manual.write_text("code,name\nA4b,Deluxe Pack: ex\nA4a,Manual override\n", encoding="utf-8")

    expansions = merge_catalogs(
        [
            Expansion(code="B1", name="Mega Rising"),
            Expansion(code="A4a", name="Secluded Springs"),
            Expansion(code="A4", name="Wisdom of Sea and Sky"),
        ],
        load_manual_catalog(manual),
    )

    assert catalog_rows(expansions) == [
        ("A4", "Wisdom of Sea and Sky"),
        ("A4a", "Manual override"),
        ("A4b", "Deluxe Pack: ex"),
        ("B1", "Mega Rising"),
    ]


def test_main_can_export_existing_json_cache_with_manual_rows(tmp_path: Path):
    cache = tmp_path / "expansions.json"
    manual = tmp_path / "manual.csv"
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
    manual.write_text("code,name\nA4b,Deluxe Pack: ex\n", encoding="utf-8")

    assert main(
        ["--cache-json", str(cache), "--manual-csv", str(manual), "--output", str(output)]
    ) == 0
    assert output.read_text(encoding="utf-8") == (
        "code,name\nA4b,Deluxe Pack: ex\nB4,Ruler of the Skies\n"
    )
