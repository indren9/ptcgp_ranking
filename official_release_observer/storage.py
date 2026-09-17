"""Fixed non-authoritative storage; immutable records and append-only receipts."""
import json
from pathlib import Path

from .parsing import integrity, sha, stable
from .reconcile import revision, reconcile

ROOT = Path(__file__).resolve().parents[1] / "data/candidates/releases/observer"


def checked(path):
    if path.resolve() != path.absolute():
        raise ValueError("observer storage cannot redirect through symlink/junction")
    return path


def immutable(path, value):
    checked(path)
    raw = (stable(value) + "\n").encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError("immutable observer record mismatch")


class Store:
    def __init__(self):
        self.root = checked(ROOT)

    def receipts(self):
        folder = checked(self.root / "receipts")
        if not folder.exists():
            return []
        rows = []
        for path in sorted(folder.glob("*.json")):
            checked(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            if sha(stable(value)) != path.stem.split("-")[-1]:
                raise ValueError("receipt integrity failed")
            rows.append(value)
        return rows

    def latest(self):
        latest = {}
        for receipt in self.receipts():
            for item in receipt["sources"]:
                oid = item["observation_id"]
                if len(oid) != 64 or any(c not in "0123456789abcdef" for c in oid):
                    raise ValueError("invalid stored observation ID")
                path = checked(self.root / "observations" / (oid + ".json"))
                observation = json.loads(path.read_text(encoding="utf-8"))
                if not integrity(observation) or observation["observation_id"] != oid:
                    raise ValueError("stored evidence integrity failed")
                latest[observation["canonical_url"]] = observation
        return latest

    def save(self, observations, coverage, retrieved_at):
        checked(self.root).mkdir(parents=True, exist_ok=True)
        lock = checked(self.root / ".write-lock")
        try:
            handle = lock.open("x")
        except FileExistsError as exc:
            raise ValueError("observer writer already active; inspect stale lock manually") from exc
        try:
            handle.close()
            for name in ("observations", "receipts", "reviews"):
                checked(self.root / name).mkdir(exist_ok=True)
            old = self.latest()
            receipts = self.receipts()
            held = {key for receipt in receipts for key in receipt["quarantined_identities"]}
            held_revisions = {key for receipt in receipts for key in receipt.get("quarantined_observations", [])}
            sources = []
            for observation in observations:
                if not integrity(observation):
                    raise ValueError("evidence integrity failed")
                prior = old.get(observation["canonical_url"])
                change, quarantine = revision(prior, observation)
                if quarantine:
                    held.update(x["provisional_identity"] for x in (prior, observation) if x and x["provisional_identity"])
                    held_revisions.add(prior["observation_id"])
                path = self.root / "observations" / (observation["observation_id"] + ".json")
                if checked(path).exists():
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    if not integrity(existing) or existing["observation_id"] != observation["observation_id"]:
                        raise ValueError("immutable observation corrupted")
                else:
                    immutable(path, observation)
                sources.append(dict(observation_id=observation["observation_id"], change=change,
                                    prior_revision=prior["observation_id"] if prior else None,
                                    quarantine_prior_candidate=quarantine))
                old[observation["canonical_url"]] = observation
            candidates = reconcile(list(old.values()))
            for candidate in candidates:
                if candidate["provisional_identity"] in held:
                    candidate.update(state="QUARANTINED", accepted_exact_timestamp=None)
                if not coverage.get("complete", False):
                    candidate.update(accepted_exact_timestamp=None, state="CANONICAL_PENDING")
                    candidate["reasons"].append("DISCOVERY_COVERAGE_DEGRADED")
            report = dict(status="NON_AUTHORITATIVE_OBSERVER_REVIEW", candidates=candidates, coverage=coverage,
                          quarantined_identities=sorted(held), quarantined_observations=sorted(held_revisions))
            immutable(self.root / "reviews" / (sha(stable(report)) + ".json"), report)
            receipt = dict(retrieved_at=retrieved_at, sources=sources, coverage=coverage,
                           quarantined_identities=sorted(held), quarantined_observations=sorted(held_revisions),
                           review_digest=sha(stable(report)))
            immutable(self.root / "receipts" / (f"{len(receipts)+1:09d}-" + sha(stable(receipt)) + ".json"), receipt)
            return report
        finally:
            checked(lock).unlink()
