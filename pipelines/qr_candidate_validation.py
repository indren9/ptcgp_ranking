"""Isolated QR V1 validation candidate; no ordering, encoding or publishing."""
from collections import Counter
import re

ENERGIES = frozenset("grass fire water lightning psychic fighting darkness metal".split())

class QRGap(ValueError):
    """Input cannot be represented faithfully; no PNG should be emitted."""

def validate_decklist(decklist, resolve):
    """resolve(set_code, collector_number) -> unique native card ID or None.

    The resolver must use a verified pinned card database. No name fallback.
    Preserve actual energies; never infer them from the cards.
    """
    if not isinstance(decklist, dict):
        raise QRGap("MISSING_DECKLIST")
    energy = decklist.get("energy")
    if (not isinstance(energy, list) or not 1 <= len(energy) <= 3
        or any(not isinstance(e, str) or e.casefold() not in ENERGIES for e in energy)
        or len({e.casefold() for e in energy}) != len(energy)):
        raise QRGap("ENERGY_UNVERIFIED")
    groups = []
    for key in ("pokemon", "trainer"):
        value = decklist.get(key, [])
        if not isinstance(value, list):
            raise QRGap("INVALID_CARD_GROUP")
        groups.extend(value)
    total = 0
    counts = Counter()
    for c in groups:
        if not isinstance(c, dict):
            raise QRGap("INVALID_CARD")
        n, s, number = c.get("count"), c.get("set"), c.get("number")
        if (type(n) is not int or n <= 0 or not isinstance(s, str) or not s
            or not isinstance(number, str) or not number):
            raise QRGap("INVALID_CARD")
        card_id = resolve(s, number)
        if not isinstance(card_id, str) or not card_id:
            raise QRGap("CARD_UNRESOLVED")
        total += n
        counts[card_id] += n
    if total != 20:
        raise QRGap("INCOMPLETE_DECKLIST")
    return tuple(sorted(counts.items())), tuple(e.casefold() for e in energy)

def safe_filename(label):
    if not isinstance(label, str) or not label.strip():
        raise QRGap("EMPTY_LABEL")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip().rstrip(". ")
    if not stem or stem.split(".")[0].upper() in (
        {"CON","PRN","AUX","NUL"} | {f"{p}{n}" for p in ("COM","LPT") for n in "123456789"}
    ):
        raise QRGap("UNSAFE_FILENAME")
    return stem + ".png"

def validate_filenames(labels):
    names = [safe_filename(x) for x in labels]
    if len({x.casefold() for x in names}) != len(names):
        raise QRGap("FILENAME_COLLISION")
    return names
