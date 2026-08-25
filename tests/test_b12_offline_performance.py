from __future__ import annotations

import warnings

import pandas as pd
import pytest

from core.consolidate import (
    apply_alias_and_aggregate,
    maxN_flat,
)
from pipelines.deck_ranking import (
    _prepare_canonical_dense_core_input,
)
from pipelines.limitless_api_acquisition import (
    _concat_or_empty,
)


def _dense_fixture():
    axis = [
        "deck-a",
        "deck-b",
        "deck-c",
    ]

    rows = [
        {
            "Deck A": "deck-a",
            "Deck B": "deck-b",
            "W": 3,
            "L": 1,
            "T": 0,
        },
        {
            "Deck A": "deck-b",
            "Deck B": "deck-a",
            "W": 1,
            "L": 3,
            "T": 0,
        },
        {
            "Deck A": "deck-a",
            "Deck B": "deck-c",
            "W": 0,
            "L": 0,
            "T": 0,
        },
        {
            "Deck A": "deck-c",
            "Deck B": "deck-a",
            "W": 0,
            "L": 0,
            "T": 0,
        },
        {
            "Deck A": "deck-b",
            "Deck B": "deck-c",
            "W": 0,
            "L": 0,
            "T": 2,
        },
        {
            "Deck A": "deck-c",
            "Deck B": "deck-b",
            "W": 0,
            "L": 0,
            "T": 2,
        },
    ]

    dense = pd.DataFrame(rows)
    dense["N"] = (
        dense["W"]
        + dense["L"]
        + dense["T"]
    )

    return dense, axis


def test_dense_fast_path_matches_legacy_output():
    dense, axis = _dense_fixture()

    legacy = apply_alias_and_aggregate(
        maxN_flat(dense),
        {},
    )

    fast = _prepare_canonical_dense_core_input(
        dense,
        axis,
    )

    pd.testing.assert_frame_equal(
        fast,
        legacy,
        check_dtype=True,
    )


def test_dense_fast_path_rejects_bad_symmetry():
    dense, axis = _dense_fixture()

    mask = (
        (dense["Deck A"] == "deck-b")
        & (dense["Deck B"] == "deck-a")
    )

    dense.loc[mask, "W"] = 99
    dense.loc[mask, "N"] = (
        dense.loc[mask, "W"]
        + dense.loc[mask, "L"]
        + dense.loc[mask, "T"]
    )

    with pytest.raises(
        ValueError,
        match="directional symmetry",
    ):
        _prepare_canonical_dense_core_input(
            dense,
            axis,
        )


def test_dense_fast_path_rejects_bad_n():
    dense, axis = _dense_fixture()

    dense.loc[0, "N"] = 999

    with pytest.raises(
        ValueError,
        match=r"N=W\+L\+T",
    ):
        _prepare_canonical_dense_core_input(
            dense,
            axis,
        )


def test_concat_all_na_has_no_futurewarning():
    columns = ("a", "b")

    frames = [
        pd.DataFrame(
            {
                "a": pd.Series(
                    [1],
                    dtype="int64",
                ),
                "b": pd.Series(
                    [None],
                    dtype="object",
                ),
            }
        ),
        pd.DataFrame(
            {
                "a": pd.Series(
                    [2],
                    dtype="int64",
                ),
                "b": pd.Series(
                    ["x"],
                    dtype="object",
                ),
            }
        ),
    ]

    with warnings.catch_warnings(
        record=True
    ) as caught:
        warnings.simplefilter(
            "always",
            FutureWarning,
        )

        out = _concat_or_empty(
            frames,
            columns,
        )

    future = [
        item
        for item in caught
        if issubclass(
            item.category,
            FutureWarning,
        )
    ]

    assert future == []
    assert out["a"].tolist() == [1, 2]
    assert pd.isna(out.loc[0, "b"])
    assert out.loc[1, "b"] == "x"
