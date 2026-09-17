"""Explicit developer CLI only. No scheduler, production hooks or canonical writer."""
import argparse
import json
from pathlib import Path

from .discovery import discover
from .network import Fetcher, FetchError, utc_now
from .parsing import observe, stable
from .reconcile import reconcile
from .storage import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--network", action="store_true", help="Explicit bounded public GET discovery")
    mode.add_argument("--html", type=Path, help="Offline source snapshot; requires --url")
    parser.add_argument("--url")
    parser.add_argument("--article-url", action="append", default=[])
    parser.add_argument("--max-sources", type=int, default=160)
    parser.add_argument("--request-budget", type=int, default=240)
    parser.add_argument("--save", action="store_true", help="Fixed ignored non-authoritative store only")
    args = parser.parse_args(argv)
    stamp = utc_now()
    observations = []
    coverage = dict(complete=True, scope="explicit offline source only", coverage_errors=[])
    if args.html:
        if not args.url:
            parser.error("--html requires --url")
        observations.append(observe(args.url, args.html.read_text(encoding="utf-8"), stamp))
    else:
        fetcher = Fetcher(budget=args.request_budget)
        known = Store().latest() if args.save else {}
        coverage = discover(fetcher, max_sources=args.max_sources, known_urls=known)
        coverage["scope"] = "bounded official category plus explicitly supplied editorial URLs"
        urls = list(dict.fromkeys(coverage["urls"] + args.article_url))
        if len(urls) > args.max_sources:
            coverage["coverage_errors"].append("SOURCE_BOUND")
        for url in urls[:args.max_sources]:
            try:
                response, chain = fetcher.get(url)
                observation = observe(chain[-1], response.body, utc_now(), redirect_chain=chain)
                observations.append(observation)
                if observation["state"] in {"CHALLENGE", "PARSER_DRIFT", "API_SCHEMA_DRIFT", "AUTHORITY_UNVERIFIED"}:
                    coverage["coverage_errors"].append(url + ": " + observation["state"])
            except FetchError as exc:
                coverage["coverage_errors"].append(url + ": " + str(exc))
        coverage["complete"] = not coverage["coverage_errors"]
        coverage["fetch_receipts"] = fetcher.receipts
    if args.save:
        report = Store().save(observations, coverage, stamp)
    else:
        candidates = reconcile(observations)
        if not coverage["complete"]:
            for c in candidates:
                c.update(state="CANONICAL_PENDING", accepted_exact_timestamp=None)
        report = dict(status="NON_AUTHORITATIVE_OBSERVER_REVIEW", candidates=candidates,
                      observations=observations, coverage=coverage)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if coverage["complete"] and not any(c["state"] == "OFFICIAL_CONFLICT" for c in report["candidates"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
