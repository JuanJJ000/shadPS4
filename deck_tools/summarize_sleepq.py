#!/usr/bin/env python3
"""Summarize opt-in sleep-queue contention counters from a shadPS4 run."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


STATS_MARKER = "Sleep queue stats:"
FIELD_PATTERN = re.compile(r"\b([a-z][a-z0-9_]*)=([0-9]+(?:\.[0-9]+)?)")
CLASS_PATTERN = re.compile(
    r"(contention_owners|contention_waiters|off_cpu_owner_ms)="
    r"\[unknown:([0-9.]+),main:([0-9.]+),workers:([0-9.]+),"
    r"movie:([0-9.]+),other:([0-9.]+)\]"
)
TOP_PATTERN = re.compile(
    r"([0-9]+):([0-9]+)acq/([0-9]+)cont/([0-9]+)ch/"
    r"(0x[0-9a-f]+)wc/([0-9.]+)wait_ms",
    re.IGNORECASE,
)
INTEGER_FIELDS = {
    "acquisitions",
    "contended",
    "wchan_changes",
    "timed_holds",
    "off_cpu_holds_over_50us",
}
REQUIRED_FIELDS = {
    "acquisitions",
    "contended",
    "wall_ms",
    "wait_total_ms",
    "wait_max_ms",
    "timed_holds",
    "hold_total_ms",
    "hold_max_ms",
    "off_cpu_total_ms",
    "off_cpu_max_ms",
    "off_cpu_holds_over_50us",
}
THREAD_CLASSES = ("unknown", "main", "workers", "movie", "other")


def resolve_log(path: Path) -> Path:
    if path.is_file():
        return path
    if not path.is_dir():
        raise FileNotFoundError(path)
    direct = path / "console.log"
    if direct.is_file():
        return direct
    candidates = sorted(path.glob("logs/*.log"), key=lambda item: item.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"no console.log or logs/*.log under {path}")
    return candidates[-1]


def parse_intervals(text: str) -> list[dict[str, object]]:
    intervals: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if STATS_MARKER not in line:
            continue
        body = line.split(STATS_MARKER, 1)[1]
        fields: dict[str, object] = {"line": line_number}
        for name, raw_value in FIELD_PATTERN.findall(body):
            fields[name] = int(raw_value) if name in INTEGER_FIELDS else float(raw_value)
        missing = REQUIRED_FIELDS.difference(fields)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"line {line_number}: missing sleep-queue fields: {names}")
        for name, *values in CLASS_PATTERN.findall(body):
            cast = float if name == "off_cpu_owner_ms" else int
            fields[name] = {
                thread_class: cast(value)
                for thread_class, value in zip(THREAD_CLASSES, values, strict=True)
            }
        fields["top"] = [
            {
                "bucket": int(bucket),
                "acquisitions": int(acquisitions),
                "contended": int(contended),
                "wchan_changes": int(changes),
                "latest_wchan": wchan.lower(),
                "wait_ms": float(wait_ms),
            }
            for bucket, acquisitions, contended, changes, wchan, wait_ms in TOP_PATTERN.findall(body)
            if int(acquisitions) or int(contended) or float(wait_ms)
        ]
        intervals.append(fields)
    return intervals


def summarize(intervals: list[dict[str, object]], tail_count: int) -> dict[str, object]:
    if not intervals:
        raise ValueError("no sleep-queue intervals found")
    selected = intervals[-tail_count:] if tail_count else intervals
    summed_fields = (
        "acquisitions",
        "contended",
        "wchan_changes",
        "timed_holds",
        "off_cpu_holds_over_50us",
    )
    totals = {
        field: sum(int(interval.get(field, 0)) for interval in selected)
        for field in summed_fields
    }
    wall_ms = sum(float(interval["wall_ms"]) for interval in selected)
    wait_ms = sum(float(interval["wait_total_ms"]) for interval in selected)
    hold_ms = sum(float(interval["hold_total_ms"]) for interval in selected)
    off_cpu_ms = sum(float(interval["off_cpu_total_ms"]) for interval in selected)
    class_totals: dict[str, dict[str, float]] = {}
    for field in ("contention_owners", "contention_waiters", "off_cpu_owner_ms"):
        class_totals[field] = {
            thread_class: round(
                sum(float(interval.get(field, {}).get(thread_class, 0)) for interval in selected),
                3,
            )
            for thread_class in THREAD_CLASSES
        }
    buckets: dict[int, dict[str, object]] = {}
    for interval in selected:
        for hot in interval["top"]:
            bucket = buckets.setdefault(
                hot["bucket"],
                {
                    "bucket": hot["bucket"],
                    "acquisitions": 0,
                    "contended": 0,
                    "wchan_changes": 0,
                    "latest_wchan": hot["latest_wchan"],
                    "wait_ms": 0.0,
                },
            )
            bucket["acquisitions"] += hot["acquisitions"]
            bucket["contended"] += hot["contended"]
            bucket["wchan_changes"] += hot["wchan_changes"]
            bucket["latest_wchan"] = hot["latest_wchan"]
            bucket["wait_ms"] += hot["wait_ms"]
    hottest = sorted(buckets.values(), key=lambda item: (-item["wait_ms"], item["bucket"]))[:5]
    for bucket in hottest:
        bucket["wait_ms"] = round(bucket["wait_ms"], 3)
    acquisitions = totals["acquisitions"]
    contended = totals["contended"]
    return {
        "intervals_available": len(intervals),
        "intervals_selected": len(selected),
        "tail_count": tail_count,
        **totals,
        "wall_ms": round(wall_ms, 3),
        "acquisition_rate": round(acquisitions * 1000.0 / wall_ms, 3) if wall_ms else 0.0,
        "contention_pct": round(contended * 100.0 / acquisitions, 3) if acquisitions else 0.0,
        "wait_total_ms": round(wait_ms, 3),
        "wait_share_pct": round(wait_ms * 100.0 / wall_ms, 3) if wall_ms else 0.0,
        "wait_max_ms": max(float(interval["wait_max_ms"]) for interval in selected),
        "hold_total_ms": round(hold_ms, 3),
        "hold_max_ms": max(float(interval["hold_max_ms"]) for interval in selected),
        "off_cpu_total_ms": round(off_cpu_ms, 3),
        "off_cpu_max_ms": max(float(interval["off_cpu_max_ms"]) for interval in selected),
        "sampled_off_cpu_share_pct": round(off_cpu_ms * 100.0 / hold_ms, 3)
        if hold_ms
        else 0.0,
        **class_totals,
        "reported_hot_buckets": hottest,
    }


def render_text(log_path: Path, result: dict[str, object]) -> str:
    lines = [
        f"log={log_path}",
        f"intervals={result['intervals_selected']}/{result['intervals_available']}",
        "acquisitions={} acquisition_rate={} contention_pct={}".format(
            result["acquisitions"], result["acquisition_rate"], result["contention_pct"]
        ),
        "wait_total_ms={} wait_share_pct={} wait_max_ms={}".format(
            result["wait_total_ms"], result["wait_share_pct"], result["wait_max_ms"]
        ),
        "timed_holds={} off_cpu_total_ms={} sampled_off_cpu_share_pct={} "
        "off_cpu_holds_over_50us={}".format(
            result["timed_holds"],
            result["off_cpu_total_ms"],
            result["sampled_off_cpu_share_pct"],
            result["off_cpu_holds_over_50us"],
        ),
    ]
    for field in ("contention_owners", "contention_waiters", "off_cpu_owner_ms"):
        values = result[field]
        lines.append(field + "=" + ",".join(f"{name}:{values[name]}" for name in THREAD_CLASSES))
    if result["reported_hot_buckets"]:
        lines.append(
            "reported_hot_buckets="
            + ", ".join(
                "{}:{}acq/{}cont/{}ch/{}wc/{}wait_ms".format(
                    item["bucket"],
                    item["acquisitions"],
                    item["contended"],
                    item["wchan_changes"],
                    item["latest_wchan"],
                    item["wait_ms"],
                )
                for item in result["reported_hot_buckets"]
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="run directory or log file")
    parser.add_argument("--tail", type=int, default=0, metavar="INTERVALS")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.tail < 0:
        parser.error("--tail must be zero or greater")
    try:
        log_path = resolve_log(args.path)
        intervals = parse_intervals(log_path.read_text(encoding="utf-8", errors="replace"))
        result = summarize(intervals, args.tail)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps({"log": str(log_path), **result}, indent=2, sort_keys=True))
    else:
        print(render_text(log_path, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
