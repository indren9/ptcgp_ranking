from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging
import time

import pandas as pd

log = logging.getLogger("ptcgp")


def run_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def write_csv_versioned(
    df: pd.DataFrame,
    base_dir: Path | str,
    prefix: str,
    *,
    changed: bool,
    index: bool = False,
) -> Path:
    """
    Always write <prefix>_latest.csv. If changed=True, also write a timestamped
    copy and return that versioned path.
    """
    dest_dir = Path(base_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    latest_path = dest_dir / f"{prefix}_latest.csv"
    df.to_csv(latest_path, index=index, encoding="utf-8")
    log.debug("CSV aggiornato: %s", latest_path)

    if changed:
        ts_path = dest_dir / f"{prefix}_{run_stamp()}.csv"
        df.to_csv(ts_path, index=index, encoding="utf-8")
        log.debug("CSV versionato (changed=True): %s", ts_path)
        return ts_path

    return latest_path


def save_plot_timestamped(fig, base_dir: Path | str, prefix: str, *, fmt: str = "png", dpi: int = 300) -> Path:
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{prefix}_{run_stamp()}.{fmt}"
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    log.debug("Plot salvato (timestamped): %s", path)
    return path


def save_plot_dual(
    fig,
    base_dir: Path | str,
    prefix: str,
    tag: str,
    *,
    fmt: str = "png",
    dpi: int = 300,
) -> tuple[Path, Path]:
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    ts_path = base_dir / f"{prefix}_{tag}_{run_stamp()}.{fmt}"
    latest_path = base_dir / f"{prefix}_latest.{fmt}"

    fig.savefig(ts_path, dpi=dpi, bbox_inches="tight")
    fig.savefig(latest_path, dpi=dpi, bbox_inches="tight")
    log.debug("Plot salvato (timestamp + latest): %s | latest: %s", ts_path, latest_path)
    return ts_path, latest_path


def write_excel_versioned(
    workbook: dict[str, pd.DataFrame],
    base_dir: Path | str,
    prefix: str,
    *,
    tag: str | None = None,
    include_latest: bool = True,
    also_versioned: bool = True,
) -> tuple[Path | None, Path | None]:
    """
    Basic multi-sheet Excel writer. The styled report writer remains in
    utils.io for now and will move in a later reporting-focused tranche.
    """
    dest_dir = Path(base_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag_part = f"_{tag}" if tag else ""
    ts_path = dest_dir / f"{prefix}{tag_part}_{ts}.xlsx" if also_versioned else None
    latest_path = dest_dir / f"{prefix}_latest.xlsx" if include_latest else None

    engine = None
    for candidate in ("openpyxl", "xlsxwriter"):
        try:
            __import__(candidate)
            engine = candidate
            break
        except Exception:
            continue
    if engine is None:
        engine = "openpyxl"

    def _write(path: Path) -> None:
        with pd.ExcelWriter(path, engine=engine) as xw:
            for sheet_name, df in workbook.items():
                df.to_excel(xw, sheet_name=sheet_name, index=False)

    if ts_path is not None:
        _write(ts_path)
        log.debug("Excel versionato: %s", ts_path)
    if latest_path is not None:
        _write(latest_path)
        log.debug("Excel latest: %s", latest_path)

    return ts_path, latest_path


def write_excel_versioned_styled(*args, **kwargs):
    from reporting.excel import write_excel_versioned_styled as legacy_writer

    return legacy_writer(*args, **kwargs)


__all__ = [
    "run_stamp",
    "write_csv_versioned",
    "save_plot_timestamped",
    "save_plot_dual",
    "write_excel_versioned",
    "write_excel_versioned_styled",
]
