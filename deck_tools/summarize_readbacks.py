#!/usr/bin/env python3
"""Summarize shadPS4 precise-readback counters from a run directory or log."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


STATS_MARKER = "Precise readback stats:"
FIELD_PATTERN = re.compile(r"\b([a-z_]+)=([0-9]+(?:\.[0-9]+)?)(?:x)?")
HOT_PATTERN = re.compile(r"(0x[0-9a-f]+):([0-9]+)\(w([0-9]+)\)", re.IGNORECASE)
INTEGER_FIELDS = {
    "window_kib",
    "requests",
    "writes",
    "reads",
    "bounded_repeats",
    "tracked_pages",
    "requested_bytes",
    "download_calls",
    "copies",
    "downloaded_bytes",
    "no_downloads",
}
REQUIRED_FIELDS = {
    "requests",
    "writes",
    "reads",
    "requested_bytes",
    "copies",
    "downloaded_bytes",
    "finish_total_ms",
    "finish_max_ms",
}


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
            raise ValueError(f"line {line_number}: missing readback fields: {names}")
        fields.setdefault("window_kib", 512)
        fields["hot"] = [
            {"address": address.lower(), "requests": int(requests), "writes": int(writes)}
            for address, requests, writes in HOT_PATTERN.findall(body)
        ]
        intervals.append(fields)
    return intervals


def summarize(intervals: list[dict[str, object]], tail_count: int) -> dict[str, object]:
    if not intervals:
        raise ValueError("no precise readback intervals found")
    selected = intervals[-tail_count:] if tail_count else intervals
    windows = sorted({int(interval["window_kib"]) for interval in selected})
    totals = {
        field: sum(int(interval[field]) for interval in selected)
        for field in (
            "requests",
            "writes",
            "reads",
            "bounded_repeats",
            "requested_bytes",
            "download_calls",
            "copies",
            "downloaded_bytes",
            "no_downloads",
        )
    }
    finish_total_ms = sum(float(interval["finish_total_ms"]) for interval in selected)
    finish_max_ms = max(float(interval["finish_max_ms"]) for interval in selected)
    wall_total_ms = sum(float(interval.get("wall_ms", 0.0)) for interval in selected)
    requested_bytes = totals["requested_bytes"]
    requests = totals["requests"]
    hot_pages: dict[str, dict[str, int]] = {}
    for interval in selected:
        for hot in interval["hot"]:
            page = hot_pages.setdefault(hot["address"], {"requests": 0, "writes": 0})
            page["requests"] += hot["requests"]
            page["writes"] += hot["writes"]
    hottest = sorted(
        (
            {"address": address, **counts}
            for address, counts in hot_pages.items()
        ),
        key=lambda page: (-page["requests"], page["address"]),
    )[:5]
    return {
        "intervals_available": len(intervals),
        "intervals_selected": len(selected),
        "tail_count": tail_count,
        "window_kib": windows[0] if len(windows) == 1 else windows,
        **totals,
        "finish_total_ms": round(finish_total_ms, 3),
        "finish_avg_ms_per_request": round(finish_total_ms / requests, 6) if requests else 0.0,
        "finish_max_ms": round(finish_max_ms, 3),
        "wall_total_ms": round(wall_total_ms, 3) if wall_total_ms else None,
        "request_rate": round(requests * 1000.0 / wall_total_ms, 3) if wall_total_ms else None,
        "finish_share_pct": round(finish_total_ms * 100.0 / wall_total_ms, 3)
        if wall_total_ms
        else None,
        "amplification": round(totals["downloaded_bytes"] / requested_bytes, 3)
        if requested_bytes
        else 0.0,
        "copies_per_request": round(totals["copies"] / requests, 3) if requests else 0.0,
        "downloaded_bytes_per_request": round(totals["downloaded_bytes"] / requests, 3)
        if requests
        else 0.0,
        "hottest_pages": hottest,
    }


def render_text(log_path: Path, result: dict[str, object]) -> str:
    window = result["window_kib"]
    window_text = str(window) if isinstance(window, int) else ",".join(map(str, window))
    lines = [
        f"log={log_path}",
        f"intervals={result['intervals_selected']}/{result['intervals_available']}",
        f"window_kib={window_text}",
        f"requests={result['requests']} writes={result['writes']} reads={result['reads']}",
        f"requested_bytes={result['requested_bytes']} downloaded_bytes={result['downloaded_bytes']}",
        f"amplification={result['amplification']}x copies_per_request={result['copies_per_request']}",
        "finish_total_ms={} finish_avg_ms_per_request={} finish_max_ms={}".format(
            result["finish_total_ms"],
            result["finish_avg_ms_per_request"],
            result["finish_max_ms"],
        ),
        f"bounded_repeats={result['bounded_repeats']} no_downloads={result['no_downloads']}",
    ]
    if result["wall_total_ms"] is not None:
        lines.append(
            "wall_total_ms={} request_rate={} finish_share_pct={}".format(
                result["wall_total_ms"], result["request_rate"], result["finish_share_pct"]
            )
        )
    if result["hottest_pages"]:
        hot = ", ".join(
            f"{page['address']}:{page['requests']}(w{page['writes']})"
            for page in result["hottest_pages"]
        )
        lines.append(f"hottest_pages={hot}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="run directory or log file")
    parser.add_argument(
        "--tail",
        type=int,
        default=0,
        metavar="INTERVALS",
        help="summarize only the last N intervals (default: all)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
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
