"""Execution DAG and per-tournament checkpoints; backend owns domain logic."""
from __future__ import annotations

from pathlib import Path
import platform
import sys
import uuid

from .model import CheckpointError, IntegrityError, RebuildError, RetryableError, Window, digest
from .store import (artifact, check_artifact, inside, load_state, now, read_result,
                    save_state, write_json, writer_lock)

STAGES = ("WINDOW_READY", "DISCOVERY", "TOURNAMENT_IDS_FROZEN", "RAW_ACQUISITION",
          "NORMALIZATION", "CORE", "MARS", "VALIDATION", "REPORT", "DONE")
FOLDERS = {"NORMALIZATION": "normalized", "CORE": "core", "MARS": "mars", "REPORT": "report"}


class Progress:
    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.interactive = self.stream.isatty()

    def emit(self, done: int, total: int, window: Window, stage: str,
             raw_done: int, raw_total: int, tournament: str = ""):
        text = (f"Overall windows {done}/{total} | {window.name} [{window.window_id}] "
                f"{window.start_utc} -> {window.end_utc or 'OPEN'} | {stage} | "
                f"RAW {raw_done}/{raw_total}" + (f" | tournament={tournament}" if tournament else ""))
        if self.interactive:
            from tqdm import tqdm
            # One complete line per checkpoint: no correctness tied to cursor control.
            fraction = raw_done / raw_total if raw_total else 0
            text = "[" + "#" * int(24 * fraction) + "-" * (24 - int(24 * fraction)) + "] " + text
            tqdm.write(text, file=self.stream)
        else:
            print(text, file=self.stream, flush=True)


class Runner:
    """Backend contract: semantics(stage), execute(stage, window, inputs, directory),
    acquire(window, item), validate_raw(tid, payload). All outputs JSON-compatible.
    Stage execution receives only checked upstream result dictionaries. Raw data
    are immutable references; the backend receives a loader with integrity checks.
    """

    def __init__(self, root: Path, windows: list[Window], backend, progress=None):
        self.root = Path(root).resolve()
        self.windows = windows
        self.backend = backend
        self.progress = progress or Progress()
        self.completed = 0
        if len({w.window_id.casefold() for w in windows}) != len(windows):
            raise RebuildError("duplicate window identities")
        # Freeze effective semantics once for the invocation, including STATUS.
        self.semantics = {stage: backend.semantics(stage) for stage in STAGES}

    def _root(self, window):
        return inside(self.root, window.window_id)

    def _state_path(self, window):
        return inside(self._root(window), "state/current.json")

    def _load(self, window):
        state = load_state(self._state_path(window), window.window_id, STAGES)
        root = self._root(window)
        if state is None and root.exists() and any(root.iterdir()):
            raise CheckpointError(f"Missing checkpoint with existing window evidence: {root}. Preserve the workspace and restore a verified checkpoint; refusing a silent restart.")
        return state or {
            "schema_version": 1, "window_id": window.window_id,
            "window": window.definition(), "window_fingerprint": window.fingerprint,
            "status": "PENDING", "current_stage": "WINDOW_READY",
            "stages": {s: {"status": "PENDING", "input_fingerprint": None,
                            "output_fingerprint": None, "started_at": None,
                            "completed_at": None, "error": None} for s in STAGES},
            "raw": {}, "tournament_ids": [], "tournament_manifest_digest": None,
            "counts": {"valid": 0, "total": 0}, "last_checkpoint": None,
            "error": None, "provenance": {"python": platform.python_version(), "runner_schema": 1},
        }

    def _save(self, window, state, *, success=False):
        if success:
            state["last_checkpoint"] = now()
        save_state(self._state_path(window), state)

    def _input(self, window, state, stage):
        index = STAGES.index(stage)
        upstream = {s: {"input": state["stages"][s]["input_fingerprint"],
                        "output": state["stages"][s]["output_fingerprint"]}
                    for s in STAGES[:index]}
        semantics = self.semantics[stage]
        previous = state["stages"][stage].get("semantics")
        compatible = getattr(self.backend, "can_reuse_semantics", None)
        if (state["stages"][stage]["status"] == "VALID" and previous != semantics
                and compatible is not None and compatible(stage, previous, semantics)):
            semantics = previous
        value = {"stage": stage, "semantics": semantics, "upstream": upstream}
        if stage == "WINDOW_READY":
            value["window"] = window.definition()
        if stage == "RAW_ACQUISITION":
            value["raw"] = {tid: {k: state["raw"].get(tid, {}).get(k) for k in ("state", "sha256", "input_fingerprint")}
                            for tid in state["tournament_ids"]}
        return digest(value)

    def _raw_token(self, item):
        return digest({"item": item, "semantics": self.semantics["RAW_ACQUISITION"]})

    def _raw_valid(self, root, record):
        return (record.get("state") == "VALID" and record.get("sha256") == record.get("artifact", {}).get("sha256")
                and check_artifact(root, record.get("artifact", {})))

    def _inspect(self, window, state):
        root = self._root(window)
        frozen = state["stages"]["TOURNAMENT_IDS_FROZEN"].get("artifact", {})
        expected = {}
        if check_artifact(root, frozen):
            expected = {item["id"]: self._raw_token(item)
                        for item in read_result(root, frozen)["tournaments"]}
        for tid, record in state["raw"].items():
            if record["state"] == "VALID" and (not self._raw_valid(root, record)
                    or (tid in expected and record["input_fingerprint"] != expected[tid])):
                record["state"] = "STALE"
                record["error"] = {"kind": "INTEGRITY_FAILURE" if not check_artifact(root, record.get("artifact", {})) else "STALE_CHECKPOINT",
                                   "message": f"raw integrity or acquisition semantics changed: {tid}"}
        invalid = False
        first = None
        for stage in STAGES:
            entry = state["stages"][stage]
            valid = (not invalid and entry["status"] == "VALID"
                     and entry["input_fingerprint"] == self._input(window, state, stage)
                     and entry["output_fingerprint"] == entry.get("artifact", {}).get("sha256")
                     and check_artifact(root, entry.get("artifact", {})))
            if not valid:
                invalid = True
                first = first or stage
                if (entry["status"] in {"VALID", "RUNNING"}
                        or entry["status"] == "FAILED"
                        and entry["input_fingerprint"] != self._input(window, state, stage)):
                    entry["status"] = "STALE"
        state["counts"] = {"total": len(state["tournament_ids"]),
                           "valid": sum(self._raw_valid(root, state["raw"].get(tid, {})) for tid in state["tournament_ids"])}
        if not invalid:
            state["status"] = "DONE"
            state["current_stage"] = "DONE"
        else:
            if state["status"] == "DONE":
                state["status"] = "STALE"
            elif any(s["status"] == "STALE" for s in state["stages"].values()) and state["status"] != "BLOCKED":
                state["status"] = "PARTIAL"
            state["current_stage"] = first
        # Render the currently supplied canonical bounds even when the saved
        # definition is stale. STATUS never persists this in-memory update.
        state["window"] = window.definition()
        return state

    def status(self):
        """Read hashes and checkpoints only. No saves, locks, backend execution/network."""
        return [self._inspect(window, self._load(window)) for window in self.windows]

    def _emit(self, window, state, tid=""):
        self.progress.emit(self.completed, len(self.windows), window, state["current_stage"],
                           state["counts"]["valid"], state["counts"]["total"], tid)

    def _generation(self, root, folder, fingerprint):
        # Unique attempts retain interrupted and stale work, even for identical inputs.
        directory = inside(root, f"{folder}/{fingerprint}-{uuid.uuid4().hex}")
        directory.mkdir(parents=True)
        return directory

    def _acquire(self, window, state, frozen):
        root = self._root(window)
        items = frozen["tournaments"]
        ids = [item["id"] for item in items]
        if ids != sorted(set(ids)) or any(not isinstance(tid, str) or not tid for tid in ids):
            raise RebuildError("frozen tournament manifest must have unique sorted non-empty IDs")
        state["tournament_ids"] = ids
        state["tournament_manifest_digest"] = digest(items)
        for item in items:
            tid = item["id"]
            record = state["raw"].setdefault(tid, {"tournament_id": tid, "state": "PENDING"})
            if record.get("input_fingerprint") != self._raw_token(item):
                record["state"] = "STALE" if record.get("artifact") else "PENDING"
        state["counts"] = {"total": len(ids), "valid": sum(self._raw_valid(root, state["raw"][tid]) for tid in ids)}
        self._save(window, state)
        for item in items:
            tid = item["id"]
            record = state["raw"][tid]
            if self._raw_valid(root, record):
                continue
            self._emit(window, state, tid)
            try:
                payload = self.backend.acquire(window, item)
                self.backend.validate_raw(tid, payload)
                directory = self._generation(root, "raw", digest(payload))
                write_json(directory / "result.json", payload)
                ref = artifact(root, directory)
                # Preserve superseded raw references as audit history too.
                prior = record.get("artifact")
                if prior:
                    record.setdefault("history", []).append(prior)
                record.update(state="VALID", artifact=ref, sha256=ref["sha256"],
                              input_fingerprint=self._raw_token(item), error=None,
                              acquisition_metadata={"acquired_at": now(), "semantics": self.semantics["RAW_ACQUISITION"]})
                state["counts"]["valid"] += 1
                self._save(window, state, success=True)
                self._emit(window, state, tid)
            except Exception as exc:
                record["state"] = "FAILED_RETRYABLE" if isinstance(exc, RetryableError) else "FAILED_BLOCKED"
                record["error"] = {"kind": getattr(exc, "kind", "BLOCKED"), "message": str(exc)}
                self._save(window, state)
                raise
        return {"tournaments": [{"id": tid, "sha256": state["raw"][tid]["sha256"]} for tid in ids]}

    def _run_window(self, window, state):
        if state["status"] == "DONE":
            return state
        root = self._root(window)
        state.update(status="PARTIAL", window=window.definition(), window_fingerprint=window.fingerprint, error=None)
        inputs = {}
        for stage in STAGES:
            entry = state["stages"][stage]
            state["current_stage"] = stage
            if entry["status"] == "VALID":
                inputs[stage] = read_result(root, entry["artifact"])
                continue
            fingerprint = self._input(window, state, stage)
            entry.update(status="RUNNING", input_fingerprint=fingerprint,
                         semantics=self.semantics[stage], started_at=now(), completed_at=None, error=None)
            self._save(window, state)
            self._emit(window, state)
            try:
                directory = self._generation(root, FOLDERS.get(stage, "state/" + stage.lower()), fingerprint)
                if stage == "WINDOW_READY":
                    # OPEN stays OPEN; an explicit frozen observation horizon is required to execute.
                    window.effective_end
                    result = window.definition()
                elif stage == "RAW_ACQUISITION":
                    result = self._acquire(window, state, inputs["TOURNAMENT_IDS_FROZEN"])
                    fingerprint = self._input(window, state, stage)
                elif stage == "DONE":
                    result = {"validated": True, "stages": {s: state["stages"][s]["output_fingerprint"] for s in STAGES[:-1]}}
                else:
                    context = {**inputs, "load_raw": lambda tid: read_result(root, state["raw"][tid]["artifact"])}
                    result = self.backend.execute(stage, window, context, directory)
                write_json(directory / "result.json", result)
                ref = artifact(root, directory)
                entry.update(status="VALID", input_fingerprint=fingerprint,
                             output_fingerprint=ref["sha256"], artifact=ref, completed_at=now())
                inputs[stage] = result
                self._save(window, state, success=True)
            except Exception as exc:
                error = {"kind": getattr(exc, "kind", "BLOCKED"), "message": str(exc)}
                entry.update(status="FAILED", error=error)
                state.update(status="PARTIAL" if isinstance(exc, RetryableError) else "BLOCKED", error=error)
                self._save(window, state)
                raise RebuildError(f"{window.window_id}/{stage}: {error['kind']}: {exc}") from exc
        self._inspect(window, state)
        if state["status"] != "DONE":
            raise IntegrityError("post-run verification failed; no DONE pointer authorized")
        self._save(window, state, success=True)
        self.completed += 1
        self._emit(window, state)
        return state

    def run(self, operation: str, window_id: str | None = None):
        if operation not in {"next", "all", "window"}:
            raise ValueError("operation must be next/all/window")
        if operation == "window" and window_id not in {w.window_id for w in self.windows}:
            raise RebuildError(f"unknown window: {window_id}")
        with writer_lock(self.root):
            states = self.status()
            self.completed = sum(s["status"] == "DONE" for s in states)
            results = []
            for window, state in zip(self.windows, states):
                if operation == "window" and window.window_id != window_id:
                    continue
                if state["status"] == "DONE":
                    continue
                results.append(self._run_window(window, state))
                if operation in {"next", "window"}:
                    break
            return results


def status_text(states):
    lines = ["TCG HISTORICAL REBUILD", f"Overall: {sum(s['status'] == 'DONE' for s in states)} / {len(states)} windows DONE",
             "WINDOW | STATUS | RAW | CORE | MARS"]
    for state in states:
        counts = state["counts"]
        lines.append(f"{state['window_id']} | {state['status']} | {counts['valid']}/{counts['total']} | "
                     f"{state['stages']['CORE']['status']} | {state['stages']['MARS']['status']}")
    for state in states:
        if state["status"] != "DONE":
            window = state["window"]
            lines.append(f"Active: {state['window_id']} {window['start_utc']} -> {window['end_utc'] or 'OPEN'}; "
                         f"stage={state['current_stage']}; checkpoint={state['last_checkpoint']}; error={state['error']}")
            break
    return "\n".join(lines)
