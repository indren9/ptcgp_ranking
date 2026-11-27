"""P2 R3 candidate: guarded publication of the 3-file private TCG bundle.

Staging/backups reside on the same volume as the OneDrive target. A process
failure triggers rollback; a hard crash leaves an explicit pending journal and
immutable prior artifacts for manual/reviewed recovery. Consumers must validate
manifest hashes: multiple os.replace operations are not a single atomic swap.
This code is NOT a proven crash-safe upgrade; WP3 fault injection is mandatory.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
from typing import Any, Callable, Mapping

from scripts.latest_completed_meta import validate_bundle
from storage.onedrive_publisher import canonical_game_root
from scripts.tcg_live_private_plan import PrivatePlanError, _verified_private_manifest
from scripts.tcg_live_latest_completed import build_plan
from sources.limitless.tournament_api.release_catalog import parse_utc_datetime


FILES = ("ranking.csv", "heatmap.png", "manifest.json")


class PrivatePublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GuardedPublicationResult:
    status: str
    target_root: str
    transaction_id: str
    new_sha256: Mapping[str, str]
    history_root: str

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "target_root": self.target_root,
                "transaction_id": self.transaction_id,
                "new_sha256": dict(self.new_sha256),
                "history_root": self.history_root}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _write_json(path: Path, obj: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _ledger(target: Path) -> Path:
    return target.parent / ".tcg-live-p2-private-history"


def _unresolved(ledger: Path) -> list[Path]:
    if not ledger.is_dir():
        return []
    issues = []
    for folder in ledger.iterdir():
        if not folder.is_dir():
            continue
        journal = folder / "journal.json"
        if not journal.is_file():
            issues.append(folder)
            continue
        try:
            state = json.loads(journal.read_text(encoding="utf-8")).get("status")
        except (ValueError, UnicodeError):
            issues.append(folder)
            continue
        if state not in {"COMMITTED", "RESTORED", "ABORTED"}:
            issues.append(folder)
    return issues


def _restore(target: Path, transaction: Path, *, mark_status: str) -> None:
    """Rollback only if every visible file is one of its recorded old/new states."""
    record = json.loads((transaction / "journal.json").read_text(encoding="utf-8"))
    if record.get("target") != str(target):
        raise PrivatePublicationError("recovery target mismatch")
    old = record.get("old") or {}
    new = record.get("new") or {}
    # Fail closed on third-party edits or other processes; do not destroy files.
    for filename in FILES:
        dest = target / filename
        visible = _sha(dest) if dest.is_file() else None
        if visible not in {old.get(filename), new.get(filename)}:
            raise PrivatePublicationError(f"unexpected concurrent change: {filename}")
        backup = transaction / "previous" / filename
        if old.get(filename) is not None:
            if not backup.is_file() or _sha(backup) != old[filename]:
                raise PrivatePublicationError(f"verified backup missing: {filename}")
    # At this point all backups and target preconditions have been validated.
    for filename in FILES:
        dest = target / filename
        previous = transaction / "previous" / filename
        if old.get(filename) is None:
            dest.unlink(missing_ok=True)
        elif not dest.is_file() or _sha(dest) != old[filename]:
            source = transaction / "previous" / filename
            temp = target / ("." + filename + ".restore-" + transaction.name)
            shutil.copy2(source, temp)
            if _sha(temp) != old[filename]:
                raise PrivatePublicationError("backup staging integrity failed")
            os.replace(temp, dest)
    record["status"] = mark_status
    _write_json(transaction / "journal.json", record)


def _abort_preparing(target: Path, transaction: Path) -> None:
    """Abort a pre-commit transaction only if every visible file is unchanged.

    PREPARING has no effect on consumer-visible files. It can lack backups;
    unlike COMMITTING recovery, never substitute missing backups here.
    """
    record = json.loads((transaction / "journal.json").read_text(encoding="utf-8"))
    if (record.get("status") != "PREPARING"
            or record.get("transaction_id") != transaction.name
            or record.get("target") != str(target)):
        raise PrivatePublicationError("invalid PREPARING recovery identity")
    old = record.get("old")
    if not isinstance(old, dict) or set(old) != set(FILES):
        raise PrivatePublicationError("incomplete PREPARING baseline")
    for filename in FILES:
        dest = target / filename
        visible = _sha(dest) if dest.is_file() else None
        if visible != old[filename]:
            raise PrivatePublicationError(f"PREPARING target changed: {filename}")
    record["status"] = "ABORTED"
    _write_json(transaction / "journal.json", record)


def recover_pending(*, meta_root: Path | str | None = None, transaction_id: str) -> str:
    """Explicitly reviewed recovery, not run automatically on ambiguous errors."""
    root = canonical_game_root("PTCG", meta_root=meta_root)
    if not re_safe_txid(transaction_id):
        raise PrivatePublicationError("invalid transaction_id")
    ledger = _ledger(root)
    txn = ledger / transaction_id
    if not txn.is_dir() or not (txn / "journal.json").is_file():
        raise PrivatePublicationError("no complete recoverable transaction journal")
    lock = ledger / ".writer-lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise PrivatePublicationError("writer lock present; cannot recover concurrently") from exc
    try:
        handle.write("P2 private recovery writer\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        if not txn.is_dir() or not (txn / "journal.json").is_file():
            raise PrivatePublicationError("pending transaction disappeared after lock")
        record = json.loads((txn / "journal.json").read_text(encoding="utf-8"))
        if record.get("status") == "PREPARING":
            _abort_preparing(root, txn)
            return "ABORTED"
        if record.get("status") not in {"STAGED", "COMMITTING", "ROLLED_BACK_PENDING"}:
            raise PrivatePublicationError("transaction is not pending recovery")
        _restore(root, txn, mark_status="RESTORED")
        return "RESTORED"
    finally:
        lock.unlink(missing_ok=True)


def re_safe_txid(value: str) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def _check_private_precondition(
    *, plan: Mapping[str, Any], target: Path,
) -> None:
    """Check private state and canonical catalog while the writer lock is held."""
    if plan.get("action") != "publish_required":
        raise PrivatePublicationError("private publication needs publish_required plan")
    guard = plan.get("private_precondition")
    if not isinstance(guard, dict) or set(guard) != {
        "target_root", "manifest_sha256", "catalog_path",
        "catalog_sha256", "planned_at_utc",
    }:
        raise PrivatePublicationError("private plan lacks a complete precondition")
    if guard["target_root"] != str(target.resolve()):
        raise PrivatePublicationError("private publication target drift")
    manifest = target / "manifest.json"
    try:
        current = _verified_private_manifest(target)
    except PrivatePlanError as exc:
        raise PrivatePublicationError(f"private snapshot corrupt after locking: {exc}") from exc
    current_hash = _sha(manifest) if current is not None else None
    if current_hash != guard["manifest_sha256"]:
        raise PrivatePublicationError("private snapshot changed since planning")
    catalog = Path(str(guard["catalog_path"]))
    if (not catalog.is_file() or _sha(catalog) != guard["catalog_sha256"]):
        raise PrivatePublicationError("private catalog changed since planning")
    try:
        started = parse_utc_datetime(guard["planned_at_utc"], field_name="planned_at_utc")
        current_plan = build_plan(catalog_path=catalog, state=None,
                                  acquisition_started_at=max(started, datetime.now(UTC)))
    except (TypeError, ValueError, OSError) as exc:
        raise PrivatePublicationError("private plan time/catalog invalid") from exc
    wanted = current_plan["latest_completed_window"]
    if any(str(wanted.get(field)) != str(plan["latest_completed_window"].get(field))
           for field in ("code", "name", "start", "end", "catalog_version")):
        raise PrivatePublicationError("private completed release changed since planning")
    # No silent downgrade or same-code lineage overwrite after the lock.
    if current is not None:
        actual_set = current.get("set") or {}
        window = (current.get("snapshot") or {}).get("collection_window") or {}
        actual_version = ((current.get("source") or {}).get("tournament_api") or {}).get("catalog_version")
        previous_identity = (actual_set.get("code"), actual_set.get("name"),
                             window.get("start"), window.get("end"), actual_version)
        wanted_identity = tuple(wanted[k] for k in ("code", "name", "start", "end", "catalog_version"))
        if previous_identity == wanted_identity:
            raise PrivatePublicationError("private snapshot already published")
        if str(actual_set.get("code") or "").casefold() == str(wanted["code"]).casefold():
            raise PrivatePublicationError("same completed code with changed private lineage")
        try:
            previous_end = parse_utc_datetime(window["end"], field_name="private previous end")
            wanted_end = parse_utc_datetime(wanted["end"], field_name="private wanted end")
        except (KeyError, TypeError, ValueError) as exc:
            raise PrivatePublicationError("private prior window invalid") from exc
        if previous_end >= wanted_end:
            raise PrivatePublicationError("private publication would regress or overwrite lineage")



def publish_private_bundle_guarded(
    *, bundle_dir: Path, plan: Mapping[str, Any],
    meta_root: Path | str | None = None,
    failure_injector: Callable[[str, str], None] | None = None,
) -> GuardedPublicationResult:
    """Validate and publish privately, with explicit backup before any replace.

    This implementation intentionally preserves *flat* legacy consumer paths.
    Atomicity at bundle level cannot be asserted without versioned-pointer
    consumers; WP3 must prove fail-closed recovery for its actual readers.
    """
    bundle_dir = bundle_dir.resolve()
    if not bundle_dir.is_dir():
        raise PrivatePublicationError("bundle_dir does not exist")
    validation_plan = {"action": "publish", "completed_set": dict(plan["latest_completed_window"])}
    manifest = validate_bundle(bundle_dir, validation_plan)
    if manifest.get("game") != "PTCG" or str(manifest.get("format", "")).lower() != "standard":
        raise PrivatePublicationError("invalid game/format in private bundle")
    if str(plan["latest_completed_window"]["code"]).startswith("TMP-"):
        raise PrivatePublicationError("temporary code cannot be a completed publication")
    target = canonical_game_root("PTCG", meta_root=meta_root)
    if target.is_symlink() or target.parent.is_symlink():
        raise PrivatePublicationError("symlink/junction publication target is not supported")
    source_hash = {name: _sha(bundle_dir / name) for name in FILES}
    ledger = _ledger(target)
    if _unresolved(ledger):
        raise PrivatePublicationError("unfinished prior publication exists; recovery required")
    ledger.mkdir(parents=True, exist_ok=True)
    lock = ledger / ".writer-lock"
    try:
        h = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise PrivatePublicationError("another private writer/recovery may be active") from exc
    try:
        h.write("P2 private publication writer\n")
        h.close()
        if _unresolved(ledger):
            raise PrivatePublicationError("unfinished prior publication detected after locking")
        _check_private_precondition(plan=plan, target=target)
        txn_id = uuid.uuid4().hex
        txn = ledger / txn_id
        try:
            (txn / "previous").mkdir(parents=True)
            (txn / "next").mkdir()
            target.mkdir(parents=True, exist_ok=True)
            old_hash = {name: (_sha(target / name) if (target / name).is_file() else None)
                        for name in FILES}
            record = {"transaction_id": txn_id, "status": "PREPARING", "target": str(target),
                      "old": old_hash, "new": source_hash}
            # Durable *before* any staging copy or backup failure injection.
            _write_json(txn / "journal.json", record)
        except Exception:
            # No target was replaced during bootstrap. An ordinary bootstrap
            # exception cannot leave an unjournaled directory blocking future
            # publications. Hard-crash-before-journal remains fail-closed.
            if txn.is_dir() and not (txn / "journal.json").exists():
                shutil.rmtree(txn)
            raise
        try:
            for name in FILES:
                current = target / name
                if current.is_file():
                    # The snapshot is pinned *before* staging starts.
                    if _sha(current) != old_hash[name]:
                        raise PrivatePublicationError(f"target changed during staging: {name}")
                    shutil.copy2(current, txn / "previous" / name)
                    if _sha(txn / "previous" / name) != old_hash[name]:
                        raise PrivatePublicationError("previous snapshot backup mismatch")
                elif old_hash[name] is not None:
                    raise PrivatePublicationError(f"target removed during staging: {name}")
                shutil.copy2(bundle_dir / name, txn / "next" / name)
                if _sha(txn / "next" / name) != source_hash[name]:
                    raise PrivatePublicationError("candidate staging hash mismatch")
                if failure_injector:
                    failure_injector("after_stage", name)
            record["status"] = "STAGED"
            _write_json(txn / "journal.json", record)
        except Exception:
            try:
                on_disk = json.loads((txn / "journal.json").read_text(encoding="utf-8"))
                if on_disk.get("status") == "PREPARING":
                    _abort_preparing(target, txn)
                elif on_disk.get("status") == "STAGED":
                    _restore(target, txn, mark_status="RESTORED")
                else:
                    raise PrivatePublicationError("unexpected journal state during staging error")
            except Exception as exc:
                raise PrivatePublicationError("staging abort uncertain; explicit recovery required") from exc
            raise
        try:
            record["status"] = "COMMITTING"
            _write_json(txn / "journal.json", record)
            # Manifest is last; readers must still verify manifest-to-blob hash.
            for name in FILES:
                dest = target / name
                visible = _sha(dest) if dest.is_file() else None
                if visible != old_hash[name]:
                    raise PrivatePublicationError(f"destination changed mid-transaction: {name}")
                temp = target / ("." + name + ".p2-" + txn_id)
                shutil.copy2(txn / "next" / name, temp)
                if _sha(temp) != source_hash[name]:
                    raise PrivatePublicationError("in-place staged hash mismatch")
                os.replace(temp, dest)
                if _sha(dest) != source_hash[name]:
                    raise PrivatePublicationError("post-replace hash mismatch")
                if failure_injector:
                    failure_injector("after_replace", name)
            # Consumer-facing readback before recording success.
            _verified_private_manifest(target)
            record["status"] = "COMMITTED"
            _write_json(txn / "journal.json", record)
        except Exception:
            try:
                _restore(target, txn, mark_status="RESTORED")
            except Exception as recover_exc:
                record["status"] = "ROLLED_BACK_PENDING"
                _write_json(txn / "journal.json", record)
                raise PrivatePublicationError("rollback incomplete; manual recovery required") from recover_exc
            raise
        return GuardedPublicationResult(
            "PASS", str(target), txn_id, source_hash, str(txn),
        )
    finally:
        lock.unlink(missing_ok=True)
