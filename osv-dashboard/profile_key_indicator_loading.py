"""Read-only KPI loading profiler. Run: cd osv-dashboard && python profile_key_indicator_loading.py

Times process-local cache misses/hits, not a cold OS filesystem cache. Does not
alter accepted data or print accounting values. Defaults to three runs per case.
"""
import argparse
from collections import defaultdict
import gzip
import json
import os
import resource
import statistics
import time

import app as application
import key_indicators as indicators
import key_indicators_service as service
from database import Company, DailyRow, Upload


def milliseconds(call, timings, name):
    start = time.perf_counter()
    try:
        return call()
    finally:
        timings[name] += (time.perf_counter() - start) * 1000


def summary(values):
    ordered = sorted(values)
    return {
        "mean_ms": round(statistics.mean(ordered), 1),
        "median_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(ordered[max(0, int(len(ordered) * .95 + .999999) - 1)], 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=int, help="ID of the intended company")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")

    with application.app.app_context():
        if args.company_id:
            company = Company.query.filter_by(id=args.company_id).one()
        else:
            matches = Company.query.filter(Company.name.ilike("%КГХ%")).all()
            if len(matches) != 1:
                parser.error("Specify --company-id; company name is ambiguous")
            company = matches[0]

        # Instrument exact runtime functions; no changes to business code or data.
        original_parse = service.parse_osv
        original_consolidated = service.parse_consolidated_osv
        original_build_source = service.build_indicator_source
        original_build_metrics = indicators.build_key_indicators
        original_passport = service.build_period_passport
        current = {}
        file_stats = defaultdict(lambda: {"ms": 0.0, "calls": 0, "bytes": 0})

        def parse_with_timing(original, path, *extra):
            started = time.perf_counter()
            try:
                return original(path, *extra)
            finally:
                item = file_stats[os.path.basename(path)]
                item["ms"] += (time.perf_counter() - started) * 1000
                item["calls"] += 1
                item["bytes"] = os.path.getsize(path)

        service.parse_osv = lambda path: parse_with_timing(original_parse, path)
        service.parse_consolidated_osv = (
            lambda path: parse_with_timing(original_consolidated, path))
        service.build_indicator_source = lambda *a, **kw: milliseconds(
            lambda: original_build_source(*a, **kw), current, "source_transform")
        indicators.build_key_indicators = lambda *a, **kw: milliseconds(
            lambda: original_build_metrics(*a, **kw), current, "metrics")
        service.build_period_passport = lambda *a, **kw: milliseconds(
            lambda: original_passport(*a, **kw), current, "passport")
        try:
            cases = [
                ("Q1", "2026-03-31", None),
                ("Q2", "2026-06-30", None),
                ("H1", "2026-06-30", "2026-03-31"),
            ]
            samples = defaultdict(list)
            meta = {}
            for label, endpoint, date_from in cases:
                for run in range(args.runs):
                    for mode in ("cold", "warm"):
                        if mode == "cold":
                            service._cache.clear()
                            application._key_indicator_payload_cache.clear()
                        current = defaultdict(float)
                        before_cpu = time.process_time()
                        before = time.perf_counter()
                        payload = application._operational_key_indicators_payload(
                            company, "quarter", endpoint, date_from)
                        current["server"] = (time.perf_counter() - before) * 1000
                        current["cpu"] = (time.process_time() - before_cpu) * 1000
                        raw = milliseconds(
                            lambda: application.app.json.dumps(payload).encode("utf-8"),
                            current, "json")
                        compressed = milliseconds(
                            lambda: gzip.compress(raw, compresslevel=5),
                            current, "gzip")
                        # Reproduce client JSON parse independently; DOM/network require browser.
                        milliseconds(lambda: json.loads(raw), current, "json_parse_python")
                        for step, value in current.items():
                            samples[(label, mode, step)].append(value)
                        meta[label] = {
                            "period": payload.get("period"),
                            "json_bytes": len(raw),
                            "gzip_bytes": len(compressed),
                        }
            source, _ = service.load_company_source(company.id, application.UPLOADS)
            manifest = source.get("_source_manifest", [])
            output = {
                "company_id": company.id,
                "company_name": company.name,
                "runs_per_case": args.runs,
                "daily_rows": DailyRow.query.filter_by(company_id=company.id).count(),
                "upload_records": Upload.query.filter_by(company_id=company.id).count(),
                "source": {
                    "accepted_files": sum(x.get("status") == "parsed" for x in manifest),
                    "accepted_bytes": sum(x.get("source_size") or 0 for x in manifest
                                          if x.get("status") == "parsed"),
                    "daily_observations": sum(len(x.get("daily", []))
                                              for x in source.get("accounts", {}).values()),
                    "balance_rows": sum(
                        len(report.get("rows") or [])
                        for x in source.get("accounts", {}).values()
                        for report in x.get("balance_reports", [])),
                },
                "periods": meta,
                "timings": {
                    label: {
                        mode: {step: summary(values) for (case, kind, step), values
                               in samples.items() if case == label and kind == mode}
                        for mode in ("cold", "warm")
                    } for label, _, _ in cases
                },
                "file_parse": [
                    {"file": name, "bytes": v["bytes"], "calls": v["calls"],
                     "mean_ms": round(v["ms"] / v["calls"], 1)}
                    for name, v in sorted(file_stats.items(),
                                          key=lambda pair: -pair[1]["ms"])
                    if v["calls"]
                ][:15],
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "notes": "cold = cleared in-process caches, OS file cache may be warm; "
                         "server excludes HTTP/auth/network/DOM. "
                         "json_parse_python is not browser JSON.parse.",
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        finally:
            service.parse_osv = original_parse
            service.parse_consolidated_osv = original_consolidated
            service.build_indicator_source = original_build_source
            indicators.build_key_indicators = original_build_metrics
            service.build_period_passport = original_passport


if __name__ == "__main__":
    main()