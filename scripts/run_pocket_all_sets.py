from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import copy
import logging
import tempfile
from typing import Iterable, Sequence

import yaml

from pipelines.deck_ranking import DeckRankingResult, EmptyDecklistError, InsufficientRankingDataError, run_deck_ranking
from reporting.logs import configure_logging
from sources.limitless.browser import chrome
from sources.limitless.client import make_session
from sources.limitless.pages.sets import (
    Expansion,
    FormatSetsCatalogEntry,
    fetch_catalog_with_policy,
    fetch_formats_with_policy,
    format_sets_cache_path,
    save_cached_format_sets,
    source_game_code,
)
from storage.paths import init_paths


log = logging.getLogger("ptcgp")


@dataclass(frozen=True)
class BatchRunResult:
    expansion: Expansion
    ok: bool
    skipped: bool = False
    result: DeckRankingResult | None = None
    error: str | None = None


def _load_yaml_config(base_dir: Path, config_path: str | Path) -> dict:
    path = Path(config_path)
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def apply_output_dir(cfg: dict, output_dir: str | Path | None) -> dict:
    run_cfg = copy.deepcopy(cfg)
    if output_dir is not None:
        run_cfg.setdefault("paths", {})["output_dir"] = str(output_dir)
    return run_cfg


def apply_format_code(cfg: dict, format_code: str | None, format_name: str | None = None) -> dict:
    run_cfg = copy.deepcopy(cfg)
    if format_code:
        source = run_cfg.setdefault("source", {})
        source["format"] = {"mode": "code", "code": str(format_code)}
        if format_name:
            source["format"]["name"] = str(format_name)
    return run_cfg


def config_for_set(cfg: dict, code: str) -> dict:
    run_cfg = copy.deepcopy(cfg)
    run_cfg.setdefault("scraping", {}).setdefault("set", {})
    run_cfg["scraping"]["set"]["mode"] = "code"
    run_cfg["scraping"]["set"]["code"] = str(code)
    return run_cfg


def config_for_format_only(cfg: dict) -> dict:
    run_cfg = copy.deepcopy(cfg)
    run_cfg.setdefault("scraping", {}).setdefault("set", {})
    run_cfg["scraping"]["set"]["mode"] = "format"
    run_cfg["scraping"]["set"]["code"] = ""
    return run_cfg


def _write_temp_config(temp_dir: Path, cfg: dict, game: str, code: str) -> Path:
    path = temp_dir / f"config_{game.lower()}_{code}.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _log_progress(enabled: bool, *, desc: str, done: int, total: int) -> None:
    if not enabled:
        return
    pct = (done / total * 100.0) if total else 100.0
    log.info("[BATCH PROGRESS] %s done=%d/%d pct=%.1f%% remaining=%d", desc, done, total, pct, max(0, total - done))


@contextmanager
def _silence_logging(enabled: bool):
    if not enabled:
        yield
        return
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def discover_expansions(base_dir: Path, cfg: dict, *, refresh: bool = False) -> list[Expansion]:
    decks_url = (cfg.get("scraping", {}) or {}).get("decks_url") or "https://play.limitlesstcg.com/decks"
    game = source_game_code(cfg, decks_url)

    paths = init_paths(base_dir, cfg, source_url=decks_url)
    scraping = cfg.get("scraping", {}) or {}
    session = make_session(
        max_retries=int(scraping.get("max_retries", 3)),
        backoff=float(scraping.get("backoff_factor", 0.7)),
        timeout=int(scraping.get("timeout_sec", 20) or scraping.get("request_timeout_sec", 20)),
    )
    try:
        catalog = fetch_catalog_with_policy(
            cfg,
            paths,
            session=session,
            ttl_override=0 if refresh else None,
            decks_url=decks_url,
        )
        if catalog:
            return [exp for exp in catalog if getattr(exp, "code", None)]

        sel_cfg = scraping.get("selenium", {}) or {}
        with chrome(headless=bool(sel_cfg.get("headless", True))) as browser:
            catalog = fetch_catalog_with_policy(
                cfg,
                paths,
                session=session,
                browser=browser,
                ttl_override=0,
                decks_url=decks_url,
            )
        return [exp for exp in catalog if getattr(exp, "code", None)]
    finally:
        session.close()


def discover_formats(base_dir: Path, cfg: dict, *, refresh: bool = False) -> list[str]:
    decks_url = (cfg.get("scraping", {}) or {}).get("decks_url") or "https://play.limitlesstcg.com/decks"
    paths = init_paths(base_dir, cfg, source_url=decks_url)
    scraping = cfg.get("scraping", {}) or {}
    session = make_session(
        max_retries=int(scraping.get("max_retries", 3)),
        backoff=float(scraping.get("backoff_factor", 0.7)),
        timeout=int(scraping.get("timeout_sec", 20) or scraping.get("request_timeout_sec", 20)),
    )
    try:
        formats = fetch_formats_with_policy(
            cfg,
            paths,
            session=session,
            ttl_override=0 if refresh else None,
            decks_url=decks_url,
        )
        return [fmt.code for fmt in formats if getattr(fmt, "code", None)]
    finally:
        session.close()


def discover_format_set_catalog(base_dir: Path, cfg: dict, *, refresh: bool = False) -> list[FormatSetsCatalogEntry]:
    decks_url = (cfg.get("scraping", {}) or {}).get("decks_url") or "https://play.limitlesstcg.com/decks"
    paths = init_paths(base_dir, cfg, source_url=decks_url)
    scraping = cfg.get("scraping", {}) or {}
    session = make_session(
        max_retries=int(scraping.get("max_retries", 3)),
        backoff=float(scraping.get("backoff_factor", 0.7)),
        timeout=int(scraping.get("timeout_sec", 20) or scraping.get("request_timeout_sec", 20)),
    )
    try:
        formats = fetch_formats_with_policy(
            cfg,
            paths,
            session=session,
            ttl_override=0 if refresh else None,
            decks_url=decks_url,
        )
        entries: list[FormatSetsCatalogEntry] = []
        for fmt in formats:
            if not getattr(fmt, "code", None):
                continue
            fmt_cfg = apply_format_code(cfg, fmt.code)
            expansions = fetch_catalog_with_policy(
                fmt_cfg,
                paths,
                session=session,
                ttl_override=0 if refresh else None,
                decks_url=decks_url,
            )
            entries.append(
                FormatSetsCatalogEntry(
                    code=fmt.code,
                    name=fmt.name,
                    is_current=fmt.is_current,
                    expansions=[exp for exp in expansions if getattr(exp, "code", None)],
                )
            )
        if entries:
            save_cached_format_sets(format_sets_cache_path(paths, cfg, decks_url), entries)
        return entries
    finally:
        session.close()


def select_expansions(
    expansions: Sequence[Expansion],
    *,
    only: Iterable[str] | None = None,
    start_at: str | None = None,
    limit: int | None = None,
    oldest_first: bool = False,
) -> list[Expansion]:
    selected = list(expansions)
    if oldest_first:
        selected.reverse()

    only_codes = {code.strip().lower() for code in (only or []) if code and code.strip()}
    if only_codes:
        selected = [exp for exp in selected if str(exp.code).lower() in only_codes]

    if start_at:
        needle = start_at.strip().lower()
        for idx, exp in enumerate(selected):
            if str(exp.code).lower() == needle:
                selected = selected[idx:]
                break
        else:
            raise ValueError(f"start-at set not found in selected catalog: {start_at}")

    if limit is not None:
        selected = selected[: max(0, int(limit))]
    return selected


def run_all_sets(
    *,
    base_dir: Path,
    cfg: dict,
    expansions: Sequence[Expansion],
    heatmap_top_n: int,
    run_scrape: bool,
    run_core: bool | None,
    run_mars: bool,
    run_heatmap: bool,
    run_report: bool,
    show_progress: bool,
    progress_mode: str = "log",
    output_dir: str | Path | None = None,
    dry_run: bool = False,
    continue_on_error: bool = False,
) -> list[BatchRunResult]:
    outcomes: list[BatchRunResult] = []
    if dry_run:
        for exp in expansions:
            outcomes.append(BatchRunResult(expansion=exp, ok=True))
        return outcomes

    decks_url = (cfg.get("scraping", {}) or {}).get("decks_url") or "https://play.limitlesstcg.com/decks"
    game = source_game_code(cfg, decks_url)

    with tempfile.TemporaryDirectory(prefix=f"ptcgp_{game.lower()}_all_") as temp_name:
        temp_dir = Path(temp_name)
        format_code = ((cfg.get("source", {}) or {}).get("format", {}) or {}).get("code") if isinstance((cfg.get("source", {}) or {}).get("format", {}), dict) else None
        progress_desc = f"{game} {format_code or 'config'}"
        total = len(expansions)
        use_progress_bar = show_progress and progress_mode == "bar"
        use_progress_log = show_progress and progress_mode != "bar"

        def run_one(index: int, exp: Expansion) -> bool:
            if not use_progress_bar:
                if getattr(exp, "code", None):
                    log.info("[BATCH] %d/%d set=%s name=%s", index, total, exp.code, exp.name)
                else:
                    log.info("[BATCH] %d/%d format-only name=%s", index, total, exp.name)

            if getattr(exp, "code", None):
                run_cfg = config_for_set(cfg, str(exp.code))
                temp_label = str(exp.code)
            else:
                run_cfg = config_for_format_only(cfg)
                temp_label = "format"
            temp_config = _write_temp_config(temp_dir, run_cfg, game, temp_label)

            try:
                with _silence_logging(use_progress_bar):
                    result = run_deck_ranking(
                        base_dir=base_dir,
                        config_path=temp_config,
                        output_dir=output_dir,
                        run_scrape=run_scrape,
                        run_core=run_core,
                        run_mars=run_mars,
                        run_heatmap=run_heatmap,
                        run_report=run_report,
                        heatmap_top_n=heatmap_top_n,
                        show_progress=False,
                    )
                outcomes.append(BatchRunResult(expansion=exp, ok=True, result=result))
            except EmptyDecklistError as exc:
                if not use_progress_bar:
                    log.warning("[BATCH SKIPPED] set=%s reason=%s", exp.code or "<format>", exc)
                outcomes.append(BatchRunResult(expansion=exp, ok=True, skipped=True, error=str(exc)))
            except InsufficientRankingDataError as exc:
                if not use_progress_bar:
                    log.warning("[BATCH SKIPPED] set=%s reason=%s", exp.code or "<format>", exc)
                outcomes.append(BatchRunResult(expansion=exp, ok=True, skipped=True, error=str(exc)))
            except Exception as exc:
                if not use_progress_bar:
                    log.exception("[BATCH FAILED] set=%s", exp.code or "<format>")
                outcomes.append(BatchRunResult(expansion=exp, ok=False, error=str(exc)))
                return continue_on_error
            finally:
                _log_progress(use_progress_log, desc=progress_desc, done=len(outcomes), total=total)
            return True

        _log_progress(use_progress_log, desc=progress_desc, done=0, total=total)
        if use_progress_bar:
            try:
                from tqdm import tqdm
            except Exception:
                use_progress_bar = False
                use_progress_log = show_progress
                _log_progress(use_progress_log, desc=progress_desc, done=0, total=total)
            else:
                with tqdm(expansions, total=total, desc=progress_desc, unit="job", dynamic_ncols=True, leave=False) as pbar:
                    for index, exp in enumerate(pbar, start=1):
                        pbar.set_postfix_str(str(exp.code or "<format>"), refresh=False)
                        if not run_one(index, exp):
                            break
                return outcomes

        if not use_progress_bar:
            for index, exp in enumerate(expansions, start=1):
                if not run_one(index, exp):
                    break
    return outcomes


def _csv_codes(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _format_codes(*, format_code: str | None, formats: str | None) -> list[str | None]:
    values = _csv_codes(formats)
    if values:
        return values
    return [format_code]


def _format_display(code: str | None, name: str | None = None) -> str:
    label = str(code or "config")
    clean_name = str(name).strip() if name else ""
    if clean_name and clean_name.lower() != label.lower():
        return f"{label} - {clean_name}"
    return label


def resolve_format_codes(
    base_dir: Path,
    cfg: dict,
    *,
    format_code: str | None,
    formats: str | None,
    refresh: bool,
) -> list[str | None]:
    values = _format_codes(format_code=format_code, formats=formats)
    if len(values) == 1 and values[0] and str(values[0]).lower() == "all":
        discovered = discover_formats(base_dir, cfg, refresh=refresh)
        if not discovered:
            raise RuntimeError("No formats found in Limitless catalog.")
        return discovered
    return values


def _summarize_outcomes(
    outcomes: Sequence[BatchRunResult],
    *,
    selected_count: int,
    label: str = "",
    include_details: bool = True,
) -> bool:
    skipped = [item for item in outcomes if item.skipped]
    ok_count = sum(1 for item in outcomes if item.ok and not item.skipped)
    failed = [item for item in outcomes if not item.ok]
    prefix = f"[BATCH DONE {label}]" if label else "[BATCH DONE]"
    log.info("%s ok=%d skipped=%d failed=%d selected=%d", prefix, ok_count, len(skipped), len(failed), selected_count)
    if include_details:
        for item in skipped:
            log.warning("[BATCH SKIP] set=%s reason=%s", item.expansion.code or "<format>", item.error)
        for item in failed:
            log.error("[BATCH ERROR] set=%s error=%s", item.expansion.code or "<format>", item.error)
    return bool(failed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_all_sets",
        description="Run the deck ranking pipeline on every available Limitless set for the selected config game.",
    )
    parser.add_argument("--base-dir", default=".", help="Project root. Default: current directory.")
    parser.add_argument("--config", default="config/pocket.yaml", help="Config YAML path.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override paths.output_dir for this batch. Relative paths are resolved from --base-dir.",
    )
    parser.add_argument(
        "--format-code",
        default=None,
        help="Force source.format.code for this batch, e.g. standard or expanded.",
    )
    parser.add_argument(
        "--formats",
        default=None,
        help="Comma-separated format codes to run in sequence, e.g. standard,expanded. 'all' means every catalog format.",
    )
    parser.add_argument("--refresh-catalog", action="store_true", help="Force refresh of the Limitless set catalog.")
    parser.add_argument("--oldest-first", action="store_true", help="Run from oldest to newest instead of catalog order.")
    parser.add_argument("--only", default="", help="Comma-separated set codes to run, e.g. A1,A1a,B3b.")
    parser.add_argument("--start-at", default=None, help="Start from this set code after ordering/filtering.")
    parser.add_argument("--limit", type=int, default=None, help="Run at most this many sets.")
    parser.add_argument("--dry-run", action="store_true", help="Print the selected set sequence without running.")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep running later sets if one set fails.")
    parser.add_argument("--heatmap-top-n", type=int, default=10, help="Number of decks to include in the WR heatmap.")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping and read existing contract files.")
    parser.add_argument("--skip-core", action="store_true", help="Skip core matrix rebuild.")
    parser.add_argument("--skip-mars", action="store_true", help="Skip MARS ranking.")
    parser.add_argument("--skip-heatmap", action="store_true", help="Skip WR heatmap generation.")
    parser.add_argument("--skip-report", action="store_true", help="Skip Excel report generation.")
    parser.add_argument("--progress", action="store_true", help="Print stable per-job batch progress lines.")
    parser.add_argument(
        "--progress-bar",
        action="store_true",
        help="Show a quiet tqdm progress bar; per-job logs are suppressed while each job runs.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.format_code and args.formats:
        parser.error("Use either --format-code or --formats, not both.")
    base_dir = Path(args.base_dir).resolve()

    configure_logging()
    cfg_base = apply_output_dir(_load_yaml_config(base_dir, args.config), args.output_dir)

    any_failed = False
    total_selected = 0
    all_outcomes: list[BatchRunResult] = []
    requested_format_values = _format_codes(format_code=args.format_code, formats=args.formats)
    requested_all_formats = (
        len(requested_format_values) == 1
        and requested_format_values[0]
        and str(requested_format_values[0]).lower() == "all"
    )
    format_catalog_by_code: dict[str, FormatSetsCatalogEntry] = {}
    if requested_all_formats:
        format_catalog = discover_format_set_catalog(base_dir, cfg_base, refresh=args.refresh_catalog)
        format_catalog_by_code = {entry.code: entry for entry in format_catalog}
        format_codes = list(format_catalog_by_code)
        if not format_codes:
            raise RuntimeError("No formats found in Limitless catalog.")
    else:
        format_codes = resolve_format_codes(
            base_dir,
            cfg_base,
            format_code=args.format_code,
            formats=args.formats,
            refresh=args.refresh_catalog,
        )

    for format_code in format_codes:
        catalog_entry = format_catalog_by_code.get(str(format_code)) if format_code else None
        format_name = catalog_entry.name if catalog_entry is not None else None
        cfg = apply_format_code(cfg_base, format_code, format_name=format_name)
        decks_url = (cfg.get("scraping", {}) or {}).get("decks_url") or "https://play.limitlesstcg.com/decks"
        game = source_game_code(cfg, decks_url)
        format_label = _format_display(format_code, format_name)
        log.info("[BATCH FORMAT] game=%s format=%s", game, format_label)

        expansions = list(catalog_entry.expansions) if catalog_entry is not None else discover_expansions(base_dir, cfg, refresh=args.refresh_catalog)
        if format_code and not expansions:
            expansions = [Expansion(code=None, name=format_name or str(format_code), is_current=True)]

        selected = select_expansions(
            expansions,
            only=_csv_codes(args.only),
            start_at=args.start_at,
            limit=args.limit,
            oldest_first=args.oldest_first,
        )
        if not selected:
            log.error("[BATCH] No %s sets selected for format=%s.", game, format_label)
            any_failed = True
            if not args.continue_on_error:
                break
            continue

        log.info(
            "[BATCH] game=%s format=%s selected sets: %s",
            game,
            format_label,
            ", ".join(str(exp.code or "<format>") for exp in selected),
        )
        outcomes = run_all_sets(
            base_dir=base_dir,
            cfg=cfg,
            expansions=selected,
            heatmap_top_n=args.heatmap_top_n,
            run_scrape=not args.skip_scrape,
            run_core=False if args.skip_core else None,
            run_mars=not args.skip_mars,
            run_heatmap=not args.skip_heatmap,
            run_report=not args.skip_report,
            show_progress=args.progress or args.progress_bar,
            progress_mode="bar" if args.progress_bar else "log",
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            continue_on_error=args.continue_on_error,
        )
        total_selected += len(selected)
        all_outcomes.extend(outcomes)
        failed = _summarize_outcomes(outcomes, selected_count=len(selected), label=f"format={format_label}")
        any_failed = any_failed or failed
        if failed and not args.continue_on_error:
            break

    if len(format_codes) > 1:
        any_failed = (
            _summarize_outcomes(
                all_outcomes,
                selected_count=total_selected,
                label="all-formats",
                include_details=False,
            )
            or any_failed
        )
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
