import logging

import pandas as pd

from core.consolidate import maxN_flat
from core.normalize import alias_coverage, build_alias_index


def test_alias_coverage_logs_at_debug(caplog):
    alias_index = build_alias_index({"Pikachu": ["Pika"]})

    with caplog.at_level(logging.INFO, logger="ptcgp"):
        alias_coverage(pd.Series(["Pika", "Mewtwo"]), alias_index)
    assert "Aliases: coverage" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="ptcgp"):
        alias_coverage(pd.Series(["Pika", "Mewtwo"]), alias_index)
    assert "Aliases: coverage" in caplog.text


def test_tie_n_consolidation_logs_at_debug(caplog):
    raw = pd.DataFrame(
        [
            {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 1, "L": 0, "T": 0, "N": 1, "Winrate": 100.0},
            {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 0, "L": 1, "T": 0, "N": 1, "Winrate": 0.0},
        ]
    )

    with caplog.at_level(logging.INFO, logger="ptcgp"):
        maxN_flat(raw)
    assert "[Tie N]" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="ptcgp"):
        maxN_flat(raw)
    assert "[Tie N]" in caplog.text
