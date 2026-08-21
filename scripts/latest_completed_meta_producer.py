from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


PUBLIC_RANKING_COLUMNS = (
    "Rank",
    "Deck",
    "Score_%",
    "MAS_%",
    "LB_%",
    "BT_%",
    "SE_%",
    "N_eff",
    "Opp_used",
    "Opp_total",
    "Coverage_%",
)
PERCENT_COLUMNS = ("Score_%", "MAS_%", "LB_%", "BT_%", "SE_%", "Coverage_%")
INTEGER_COLUMNS = ("Rank", "N_eff", "Opp_used", "Opp_total")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_casefold_file(run_dir: Path, relative_path: str) -> Path:
    direct = run_dir / relative_path
    if direct.is_file():
        return direct

    wanted = relative_path.replace("\\", "/").casefold()
    matches = [
        path
        for path in run_dir.rglob("*")
        if path.is_file() and path.relative_to(run_dir).as_posix().casefold() == wanted
    ]
    if len(matches) != 1:
        raise FileNotFoundError(
            f"Expected exactly one {relative_path!r} below {run_dir}; found {len(matches)}"
        )
    return matches[0]


def _git_revision(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    revision = result.stdout.strip()
    return revision or None


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _load_plan(path: Path) -> tuple[str, str]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    completed = plan.get("completed_set") or {}
    code = str(completed.get("code") or "").strip()
    name = str(completed.get("name") or "").strip()
    if not code or not name:
        raise ValueError("Publication plan must identify the completed set code and name")
    if plan.get("action") != "publish":
        raise ValueError(f"Publication plan action must be 'publish', got {plan.get('action')!r}")
    return code, name


def _source_urls_match_scope(frame, *, code: str) -> None:
    from urllib.parse import parse_qs, urlparse

    if "URL" not in frame.columns or frame.empty:
        raise ValueError("Source decklist must contain non-empty URL values")

    scopes = set()
    for value in frame["URL"].dropna().astype(str):
        query = parse_qs(urlparse(value).query)
        scopes.add(
            (
                str((query.get("game") or [""])[0]).upper(),
                str((query.get("format") or [""])[0]).lower(),
                str((query.get("set") or [""])[0]).casefold(),
            )
        )
    expected = {("POCKET", "standard", code.casefold())}
    if scopes != expected:
        raise ValueError(f"Source decklist scope {sorted(scopes)!r} does not match {sorted(expected)!r}")


def _normalize_ranking(ranking):
    import pandas as pd

    frame = ranking.copy()
    if "Rank" not in frame.columns:
        frame = frame.reset_index()
    missing = [column for column in PUBLIC_RANKING_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Ranking is missing public columns: {', '.join(missing)}")

    frame = frame.loc[:, PUBLIC_RANKING_COLUMNS].copy()
    for column in PERCENT_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise").round(4)
    for column in INTEGER_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("int64")

    if frame.isna().any().any():
        raise ValueError("Public ranking cannot contain empty cells")
    if frame["Deck"].astype(str).str.strip().eq("").any():
        raise ValueError("Public ranking cannot contain blank deck names")
    if frame["Deck"].duplicated().any() or frame.duplicated().any():
        raise ValueError("Public ranking contains duplicate decks or rows")
    if frame["Rank"].tolist() != list(range(1, len(frame) + 1)):
        raise ValueError("Public ranking Rank must be contiguous and start at 1")
    if not frame["Score_%"].is_monotonic_decreasing:
        raise ValueError("Public ranking must be sorted by Score_% descending")
    return frame


def _reconcile_ranking(source_ranking, regenerated_ranking, *, atol: float = 1e-9) -> float:
    import numpy as np
    import pandas as pd

    expected = source_ranking.copy()
    if "Rank" in expected.columns:
        expected = expected.set_index("Rank")
    actual = regenerated_ranking.copy()
    if "Rank" in actual.columns:
        actual = actual.set_index("Rank")

    if expected["Deck"].astype(str).tolist() != actual["Deck"].astype(str).tolist():
        raise ValueError("Regenerated ranking deck order does not match the source run")

    numeric_columns = [
        column
        for column in expected.select_dtypes(include="number").columns
        if column in actual.columns
    ]
    if not numeric_columns:
        raise ValueError("Source ranking has no numeric columns to reconcile")
    expected_values = expected[numeric_columns].apply(pd.to_numeric, errors="raise").to_numpy(float)
    actual_values = actual[numeric_columns].apply(pd.to_numeric, errors="raise").to_numpy(float)
    max_diff = float(np.max(np.abs(expected_values - actual_values)))
    if not np.allclose(expected_values, actual_values, rtol=0.0, atol=atol, equal_nan=True):
        raise ValueError(
            f"Regenerated ranking differs from the source run (max absolute difference {max_diff:.3g})"
        )
    return max_diff


def _markdown_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _fragment(*, ranking, manifest: Mapping[str, Any]) -> str:
    snapshot = manifest["snapshot"]
    metrics = manifest["analysis"]
    rows = [
        "## See MARS in action",
        "",
        f"### Latest completed Pocket meta: {snapshot['set']['code']} — {snapshot['set']['name']}",
        "",
        "`Pokémon TCG Pocket` "
        f"`{snapshot['format'].title()}` "
        f"`{metrics['core_decks']} decks` "
        f"`{metrics['decisive_matches']:,} decisive matches` "
        f"`{metrics['coverage_pct']['min']:.2f}–{metrics['coverage_pct']['max']:.2f}% coverage`",
        "",
        "![Observed win-rate heatmap for the top 10 MARS decks](public/latest-meta/heatmap.png)",
        "",
        "| Rank | Deck | Score % | MAS % | LB % | BT % | Coverage % |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in ranking.head(10).iterrows():
        rows.append(
            f"| {int(row['Rank'])} | {_markdown_escape(row['Deck'])} | "
            f"{row['Score_%']:.2f} | {row['MAS_%']:.2f} | {row['LB_%']:.2f} | "
            f"{row['BT_%']:.2f} | {row['Coverage_%']:.2f} |"
        )

    rows.extend(
        [
            "",
            "[Download the full ranking CSV](public/latest-meta/ranking.csv) · "
            "[Inspect the provenance manifest](public/latest-meta/manifest.json) · "
            "[Read the MARS methodology](MARS_explained.md)",
            "",
            "`MAS_%` is posterior-smoothed performance against the observed meta; `LB_%` subtracts the "
            "configured uncertainty penalty. `BT_%` is regularized Bradley–Terry strength across the "
            "matchup graph. `Score_%` maps the standardized LB/BT composite through the normal CDF and "
            "is not a match win probability. `Coverage_%` is the share of core opponents with observed "
            "decisive matchup evidence.",
            "",
            "> [!IMPORTANT]",
            "> MARS is an analytical ranking of this observed, completed meta—not a tournament forecast. "
            "Sparse matchups, player skill, and later metagame shifts remain outside the score.",
            "",
            "Built from public tournament data provided by [Limitless TCG](https://limitlesstcg.com/). "
            "See the official [Limitless developer guide](https://docs.limitlesstcg.com/developer). "
            "This independent project is not affiliated with or endorsed by Limitless TCG.",
        ]
    )
    return "\n".join(rows) + "\n"


def build_bundle(
    *,
    source_run: Path,
    config_path: Path,
    bundle_dir: Path,
    set_code: str,
    set_name: str,
    acquired_on: str,
    source_revision: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    import matplotlib.pyplot as plt
    import pandas as pd
    import yaml

    from domain.expansions import Expansion
    from pipelines.deck_ranking import _build_core_matrices, _run_mars_stage
    from reporting.plots import show_wr_heatmap
    from storage.paths import ProjectPaths

    repo_root = Path.cwd().resolve()
    source_run = source_run.resolve()
    config_path = config_path.resolve()
    bundle_dir.mkdir(parents=True, exist_ok=True)
    try:
        datetime.strptime(acquired_on, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("acquired_on must use YYYY-MM-DD") from exc

    source_paths = {
        "decklist": _find_casefold_file(source_run, "decklists/raw/decklist_raw_latest.csv"),
        "top_meta": _find_casefold_file(source_run, "decklists/top_meta/top_meta_decklist_latest.csv"),
        "matchups": _find_casefold_file(source_run, "matchups/raw/matchup_raw_latest.csv"),
        "ranking": _find_casefold_file(source_run, "rankings/mars/mars_ranking_latest.csv"),
    }
    decklist = pd.read_csv(source_paths["decklist"])
    top_meta = pd.read_csv(source_paths["top_meta"])
    matchups = pd.read_csv(source_paths["matchups"])
    source_ranking = pd.read_csv(source_paths["ranking"])
    _source_urls_match_scope(decklist, code=set_code)

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if str(((cfg.get("source") or {}).get("game") or "")).upper() != "POCKET":
        raise ValueError("Latest completed meta producer requires a Pocket configuration")
    cfg.setdefault("saving", {})["output_profile"] = "user"

    with tempfile.TemporaryDirectory(prefix="ptcgp-latest-meta-") as temp_name:
        temp_root = Path(temp_name)
        paths = ProjectPaths(
            base=repo_root,
            output_root=temp_root,
            outputs=temp_root / "POCKET" / "standard",
            cache=repo_root / "cache" / "requests",
            logs=repo_root / "logs",
        )
        expansion = Expansion(code=set_code, name=set_name)
        core_frames, _, core_diag = _build_core_matrices(
            cfg=cfg,
            paths=paths,
            exp=expansion,
            df_matchup_raw=matchups,
            df_top_meta=top_meta,
        )
        mars_frames, _, mars_diag = _run_mars_stage(
            cfg=cfg,
            paths=paths,
            exp=expansion,
            score_df=core_frames["score_flat"],
            wr_matrix=core_frames["wr_matrix"],
            n_dir_matrix=core_frames["n_dir_matrix"],
            top_meta_df=top_meta,
        )

    regenerated = mars_frames["mars_ranking"]
    max_diff = _reconcile_ranking(source_ranking, regenerated)
    public_ranking = _normalize_ranking(regenerated)
    ranking_path = bundle_dir / "ranking.csv"
    public_ranking.to_csv(ranking_path, index=False, encoding="utf-8", lineterminator="\n")

    heatmap_path = bundle_dir / "heatmap.png"
    figure, _, heatmap_frame = show_wr_heatmap(
        regenerated,
        wr=core_frames["wr_matrix"],
        top_n=10,
        annot=True,
        fmt=".1f",
        title=f"Observed win rate — MARS Top 10 · {set_code} {set_name}",
    )
    try:
        figure.savefig(heatmap_path, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    finally:
        plt.close(figure)

    coverage = public_ranking["Coverage_%"]
    auto_k = (mars_diag.get("mars_diag") or {}).get("AUTO_K", {})
    if not auto_k:
        auto_k = (mars_diag.get("AUTO_K") or {})
    k_used = float(auto_k.get("K_used", auto_k.get("K_star")))
    decisive_matches = int(round(float(regenerated["N_eff"].sum()) / 2.0))
    revision = source_revision or _git_revision(repo_root)
    timestamp = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    canonical_url = (
        f"https://play.limitlesstcg.com/decks?game=POCKET&format=standard&set={set_code}"
    )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "artifact": "latest-completed-meta",
        "generated_at": timestamp,
        "game": "POCKET",
        "format": "standard",
        "set": {"code": set_code, "name": set_name},
        "snapshot": {
            "game": {"code": "POCKET", "name": "Pokémon TCG Pocket"},
            "format": "standard",
            "set": {"code": set_code, "name": set_name},
            "acquired_on": acquired_on,
            "collection_window": None,
            "collection_scope": (
                "Set-level aggregate deck and matchup statistics available on the Limitless deck pages "
                "at acquisition time; the cached inputs do not encode an exact tournament date window."
            ),
        },
        "source": {
            "provider": "Limitless TCG",
            "decks_url": canonical_url,
            "developer_documentation": "https://docs.limitlesstcg.com/developer",
            "terms_of_service": "https://play.limitlesstcg.com/tos",
            "attribution": "Built from public tournament data provided by Limitless TCG.",
            "affiliation": "This project is independent and is not affiliated with or endorsed by Limitless TCG.",
            "data_license": "No data license is asserted by this repository.",
            "aggregation": "Only aggregate deck-archetype statistics are published.",
            "contains_personal_data": False,
        },
        "configuration": {
            "profile": _repo_relative(config_path, repo_root),
            "profile_sha256": _sha256(config_path),
            "source_revision": revision,
            "top_meta": cfg.get("top_meta") or {},
            "candidate_pool": ((cfg.get("analysis") or {}).get("candidate_pool") or {}),
            "nan_filter": cfg.get("nan_filter") or {},
            "mars": cfg.get("mars") or {},
            "runtime": {"K_used": k_used},
        },
        "inputs": {
            key: {
                "file": path.name,
                "rows": int(len(frame)),
                "sha256": _sha256(path),
            }
            for key, path, frame in (
                ("decklist_aggregate", source_paths["decklist"], decklist),
                ("top_meta_aggregate", source_paths["top_meta"], top_meta),
                ("matchup_aggregate", source_paths["matchups"], matchups),
                ("source_ranking", source_paths["ranking"], source_ranking),
            )
        },
        "analysis": {
            "deck_archetypes_in_source": int(len(decklist)),
            "top_meta_decks": int(len(top_meta)),
            "core_decks": int(len(public_ranking)),
            "post_filter_matchup_rows": int(len(core_frames["score_flat"])),
            "decisive_matches": decisive_matches,
            "coverage_pct": {
                "min": round(float(coverage.min()), 4),
                "median": round(float(coverage.median()), 4),
                "max": round(float(coverage.max()), 4),
            },
            "candidate_pool_decks": int(core_diag["axis0_count"]),
            "dropped_by_nan_filter": int(core_diag["nan_filter"]["dropped_count"]),
        },
        "methodology": {
            "name": "MARS — Meta-Adjusted, Regularized Score",
            "score_mapping": "Score_% = 100 * Phi(z_comp)",
            "score_interpretation": "Percentile-like composite score, not a match win probability.",
            "ties_in_directional_win_rates": "excluded",
        },
        "provenance": {
            "reproduced_with_current_code": True,
            "source_ranking_max_abs_difference": max_diff,
            "ranking_heatmap_same_recomputed_core": True,
        },
        "outputs": {
            "ranking": {
                "file": "ranking.csv",
                "rows": int(len(public_ranking)),
                "columns": list(PUBLIC_RANKING_COLUMNS),
                "percent_precision_decimals": 4,
                "sha256": _sha256(ranking_path),
            },
            "heatmap": {
                "file": "heatmap.png",
                "top_n": int(len(heatmap_frame)),
                "orientation": "row deck versus column opponent",
                "sha256": _sha256(heatmap_path),
            },
        },
        "limitations": [
            "The source inputs do not encode an exact tournament start/end window.",
            "Coverage and match volume vary by deck; low-volume cells can be volatile.",
            "The ranking describes the observed completed meta and is not a tournament forecast.",
        ],
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (bundle_dir / "fragment.md").write_text(
        _fragment(ranking=public_ranking, manifest=manifest),
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reproduce and package the single latest completed Pocket meta snapshot."
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/pocket.yaml"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--acquired-on", required=True, help="Source acquisition date in YYYY-MM-DD format")
    parser.add_argument("--source-revision", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code, name = _load_plan(args.plan)
    manifest = build_bundle(
        source_run=args.source_run,
        config_path=args.config,
        bundle_dir=args.bundle,
        set_code=code,
        set_name=name,
        acquired_on=args.acquired_on,
        source_revision=args.source_revision,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
