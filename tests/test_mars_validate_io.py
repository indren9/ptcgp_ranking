"""AR-01: fail-closed regressions; missing policy is characterized, not expanded."""
import hashlib
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

import mars.pipeline as pipeline
from mars.config import MARSConfig
from mars.validate_io import validate_contract

WR_ISSUE = "WR symmetry off >1.0pp"
N_ISSUE = "n_dir not symmetric"


def frames(size=2, missing=False):
    axis = list("ABCD"[:size])
    wr = pd.DataFrame(np.nan, index=axis, columns=axis)
    counts = wr.copy(deep=True)
    rows = []
    for i, a in enumerate(axis):
        for j in range(i + 1, size):
            b = axis[j]
            if missing and (i, j) == (0, 1):
                continue
            w, l = 6 + i, 4 + j
            wr.loc[a, b], wr.loc[b, a] = 100*w/(w+l), 100*l/(w+l)
            counts.loc[a, b] = counts.loc[b, a] = w + l
            rows.extend([{"Deck A": a, "Deck B": b, "W": w, "L": l},
                         {"Deck A": b, "Deck B": a, "W": l, "L": w}])
    return wr, counts, pd.DataFrame(rows)


@pytest.fixture
def original_validator():
    # Exact mars/validate_io.py from main ff12d9ee4b5fa0a5fa2617391447cbee9ada3034.
    # Git blob: 09edad81ba7f5df8e2eb8909b40116887ec01048.
    source_file = (Path(__file__).resolve().parent / "fixtures" /
                   "mars_validate_io_original_ff12d9ee.py")
    source = source_file.read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "dc3d8263e23adef583619f683a3ad2f2cf93daed9cdc8afc3caeb5c296b7623a"
    ), "AR-01 original baseline SHA-256 mismatch"
    blob = b"blob " + str(len(source)).encode("ascii") + bytes([0]) + source
    assert hashlib.sha1(blob).hexdigest() == (
        "09edad81ba7f5df8e2eb8909b40116887ec01048"
    ), "AR-01 original baseline Git blob mismatch"
    namespace = {}
    exec(compile(source, str(source_file), "exec"), namespace)
    return namespace["validate_contract"]


@pytest.mark.parametrize("size", [2, 3])
def test_valid_contract_and_no_mutation(size):
    wr, counts, _ = frames(size)
    before = (wr.copy(deep=True), counts.copy(deep=True))
    assert validate_contract(wr, counts) == {"ok": True, "issues": []}
    for actual, expected in zip((wr, counts), before):
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)


@pytest.mark.parametrize("left,right,issues", [
    (60., 60., [WR_ISSUE]),
    (60., 41., []), (60., 39., []),
    (60., 40.5, []), (60., 39.5, []),
    (60., 41.000001, [WR_ISSUE]), (60., 38.999999, [WR_ISSUE]),
])
def test_wr_threshold(left, right, issues):
    wr, counts, _ = frames()
    wr.iloc[0, 1], wr.iloc[1, 0] = left, right
    assert validate_contract(wr, counts) == {"ok": not issues, "issues": issues}


@pytest.mark.parametrize("left,right", [(10., 11.), (10., 10.000000001), (np.nan, 10.)])
def test_counts_asymmetry(left, right):
    wr, counts, _ = frames()
    counts.iloc[0, 1], counts.iloc[1, 0] = left, right
    assert validate_contract(wr, counts) == {"ok": False, "issues": [N_ISSUE]}


def test_multiple_diagnostics_order():
    wr, counts, _ = frames()
    wr.iloc[0, 1] = wr.iloc[1, 0] = 60.
    counts.iloc[0, 1], counts.iloc[1, 0] = 10., 11.
    assert validate_contract(wr, counts) == {
        "ok": False, "issues": [WR_ISSUE, N_ISSUE]}


@pytest.mark.parametrize("matrix,message", [(0, "filtered_wr diag not NaN"),
                                            (1, "n_dir diag not NaN")])
def test_diagonals(matrix, message):
    wr, counts, _ = frames()
    (wr, counts)[matrix].iloc[0, 0] = 0.
    assert validate_contract(wr, counts) == {
        "ok": False, "issues": [message, WR_ISSUE] if matrix == 0 else [message]}


@pytest.mark.parametrize("case", ["shape", "axis", "reorder"])
def test_incompatible_contracts_not_realigned(case):
    wr, counts, _ = frames(3)
    if case == "shape":
        counts = counts.iloc[:2, :2]
    elif case == "axis":
        counts = counts.rename(index={"A": "Z"}, columns={"A": "Z"})
    else:
        counts = counts.loc[list("CBA"), list("CBA")]
    before = (wr.copy(deep=True), counts.copy(deep=True))
    result = validate_contract(wr, counts)
    assert result["ok"] is False
    assert ("shape mismatch" if case == "shape" else "axis mismatch") in result["issues"]
    for actual, expected in zip((wr, counts), before):
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)


@pytest.mark.parametrize("case", ["both_missing", "one_wr_missing", "nan_zero", "zero_zero"])
def test_existing_missing_policy(case, original_validator):
    wr, counts, _ = frames()
    if case == "both_missing":
        wr.iloc[0, 1] = wr.iloc[1, 0] = np.nan
        counts.iloc[0, 1] = counts.iloc[1, 0] = np.nan
    elif case == "one_wr_missing":
        wr.iloc[0, 1] = np.nan
    elif case == "nan_zero":
        counts.iloc[0, 1], counts.iloc[1, 0] = np.nan, 0.
    else:
        counts.iloc[0, 1] = counts.iloc[1, 0] = 0.
    assert validate_contract(wr, counts) == original_validator(wr, counts) == {
        "ok": True, "issues": []}


@pytest.mark.parametrize("case", ["wr", "counts", "both"])
def test_run_mars_stops_before_science(case, monkeypatch):
    wr, counts, flat = frames()
    if case in ("wr", "both"):
        wr.iloc[0, 1] = wr.iloc[1, 0] = 60.
    if case in ("counts", "both"):
        counts.iloc[0, 1], counts.iloc[1, 0] = 10., 11.
    guards = []
    for name in ("blend_meta", "auto_k_cv", "posterior_dir", "mas_se_lb",
                 "bt_soft", "compose", "coverage_tables"):
        guard = Mock(side_effect=AssertionError("scientific stage reached: " + name))
        monkeypatch.setattr(pipeline, name, guard)
        guards.append(guard)
    with pytest.raises(ValueError, match="Contract validation failed"):
        pipeline.run_mars(wr, counts, flat, None, MARSConfig())
    for guard in guards:
        guard.assert_not_called()


def assert_equal(actual, expected):
    if isinstance(actual, pd.DataFrame):
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    elif isinstance(actual, pd.Series):
        pd.testing.assert_series_equal(actual, expected, check_exact=True)
    elif isinstance(actual, np.ndarray):
        np.testing.assert_array_equal(actual, expected)
    elif isinstance(actual, dict):
        assert actual.keys() == expected.keys()
        for key in actual:
            assert_equal(actual[key], expected[key])
    elif isinstance(actual, (list, tuple)):
        assert type(actual) is type(expected) and len(actual) == len(expected)
        for a, b in zip(actual, expected):
            assert_equal(a, b)
    elif isinstance(actual, (float, np.floating)) and math.isnan(actual):
        assert math.isnan(expected)
    else:
        assert actual == expected


@pytest.mark.parametrize("size,missing", [(2, False), (3, False), (3, True)])
def test_scientific_equivalence(size, missing, original_validator, monkeypatch):
    wr, counts, flat = frames(size, missing)
    before = [frame.copy(deep=True) for frame in (wr, counts, flat)]
    cfg = MARSConfig(AUTO_K=True, SEED=42, BOOT_N=8, MAX_BT_ITER=100,
                     AUTO_GAMMA=False, GAMMA_META_BLEND=0.30)
    with monkeypatch.context() as baseline:
        baseline.setattr(pipeline, "validate_contract", original_validator)
        expected = pipeline.run_mars(wr, counts, flat, None, cfg)
    actual = pipeline.run_mars(wr, counts, flat, None, cfg)
    assert_equal(actual, expected)
    # Includes exact ranking/order, AUTO-K/META/BT, coverage and missing.
    for a, b in zip((wr, counts, flat), before):
        pd.testing.assert_frame_equal(a, b, check_exact=True)
    for a, b in zip((actual[0], actual[2], actual[3]),
                    (expected[0], expected[2], expected[3])):
        assert a.to_csv().encode("utf-8") == b.to_csv().encode("utf-8")


@pytest.mark.parametrize("case", ["wr", "counts"])
def test_orchestrator_propagates_without_save(case, tmp_path, monkeypatch):
    import pipelines.deck_ranking as orchestrator

    wr, counts, flat = frames()
    if case == "wr":
        wr.iloc[0, 1] = wr.iloc[1, 0] = 60.
    else:
        counts.iloc[0, 1], counts.iloc[1, 0] = 10., 11.
    monkeypatch.setattr(orchestrator, "_top_meta_for_mars", lambda *a, **k: None)
    save = Mock(side_effect=AssertionError("ranking save reached"))
    report = Mock(side_effect=AssertionError("report reached"))
    monkeypatch.setattr(orchestrator, "_save_csv_artifact", save)
    monkeypatch.setattr(orchestrator, "write_mars_matchup_report", report)
    stage = Mock(side_effect=AssertionError("meta blend reached"))
    monkeypatch.setattr(pipeline, "blend_meta", stage)
    # Actual run_mars_core remains bound to the real run_mars implementation.
    with pytest.raises(ValueError, match="Contract validation failed"):
        orchestrator._run_mars_stage(
            cfg={"mars": {"SEED": 42}}, paths=SimpleNamespace(base=tmp_path),
            exp=SimpleNamespace(code="SYNTHETIC", name="AR01"),
            wr_matrix=wr, n_dir_matrix=counts, score_df=flat,
            top_meta_df=pd.DataFrame())
    save.assert_not_called()
    report.assert_not_called()
    stage.assert_not_called()
    assert list(tmp_path.iterdir()) == []
