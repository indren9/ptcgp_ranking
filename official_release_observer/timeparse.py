"""Strict source-scoped civil-time parsing. No historical-hour fallback."""
from datetime import datetime, timezone
from importlib import resources, metadata
import re
from zoneinfo import ZoneInfo

MONTHS = {m.lower(): n for n, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
DATE = r"(?:(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?(?P<month>" + "|".join(MONTHS) + r")\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4})"
CLOCK = r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]\.?m\.?)\s*(?P<zone>(?:UTC)?[+-]\d{2}:\d{2}|[A-Za-z]+)?"
PATTERN = re.compile(r"(?:Release Date:\s*)?" + DATE + r"(?:\s*(?:[-–—]|,?\s+at)\s*" + CLOCK + r"(?:\s*\((?:(?P<utcdate>\d{4}-\d{2}-\d{2})\s+)?(?P<utc>\d{2}:\d{2})(?::(?P<utcsec>\d{2}))?\s*UTC\))?)?\.?", re.I)


def pacific():
    # Load exactly the named installed package, not an unversioned OS fallback.
    version = metadata.version("tzdata")
    with resources.files("tzdata.zoneinfo").joinpath("America/Los_Angeles").open("rb") as handle:
        zone = ZoneInfo.from_file(handle, key="America/Los_Angeles")
    return zone, "tzdata:" + version


def extract_time(text):
    result = dict(state="TIME_UNPARSEABLE", date=None, raw_local=text, raw_zone=None,
                  explicit_utc=None, local=None, calculated_utc=None, tzdb_version=None)
    match = PATTERN.fullmatch(text.strip())
    if not match:
        return result
    g = match.groupdict()
    try:
        civil = datetime(int(g["year"]), MONTHS[g["month"].lower()], int(g["day"]))
        result["date"] = civil.date().isoformat()
        if g["weekday"] and civil.strftime("%A").lower() != g["weekday"].lower():
            return dict(result, state="OFFICIAL_CONFLICT")
        if g["hour"] is None:
            return dict(result, state="DATE_CONFIRMED")
        hour, minute = int(g["hour"]), int(g["minute"] or 0)
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            return result
        hour = hour % 12 + (12 if g["ampm"].lower().startswith("p") else 0)
        civil = civil.replace(hour=hour, minute=minute)
        zone_text = (g["zone"] or "").upper()
        result.update(raw_zone=zone_text or None,
                      explicit_utc=((g["utcdate"] + " ") if g["utcdate"] else "") +
                      g["utc"] + (":" + g["utcsec"] if g["utcsec"] else "") if g["utc"] else None)
        if zone_text not in {"PT", "PDT", "PST", "UTC", "-07:00", "-08:00", "UTC-07:00", "UTC-08:00"}:
            return dict(result, state="TIMEZONE_AMBIGUOUS")
        zone, version = pacific()
        result["tzdb_version"] = version
        candidates = []
        for fold in (0, 1):
            local = civil.replace(tzinfo=timezone.utc if zone_text == "UTC" else zone, fold=fold)
            if local.astimezone(timezone.utc).astimezone(local.tzinfo).replace(tzinfo=None) != civil:
                continue
            if zone_text in {"PDT", "PST"} and local.tzname() != zone_text:
                continue
            if zone_text.endswith(("-07:00", "-08:00")) and local.utcoffset().total_seconds() != int(zone_text[-6:-3]) * 3600:
                continue
            if all(c.utcoffset() != local.utcoffset() for c in candidates):
                candidates.append(local)
        if not candidates:
            return dict(result, state="INVALID_LOCAL_TIME")
        if g["utc"]:
            def matches(local):
                utc = local.astimezone(timezone.utc)
                return (utc.strftime("%H:%M") == g["utc"] and
                        (g["utcsec"] is None or g["utcsec"] == "00") and
                        (g["utcdate"] is None or utc.date().isoformat() == g["utcdate"]))
            candidates = [c for c in candidates if matches(c)]
            if not candidates:
                return dict(result, state="OFFICIAL_CONFLICT")
        if len(candidates) != 1:
            return dict(result, state="TIMEZONE_AMBIGUOUS")
        local = candidates[0]
        return dict(result, state="EXACT", local=local.isoformat(),
                    calculated_utc=local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    except (ValueError, OSError, ModuleNotFoundError, metadata.PackageNotFoundError):
        return dict(result, state="TIMEZONE_UNAVAILABLE" if result["date"] else "TIME_UNPARSEABLE")
