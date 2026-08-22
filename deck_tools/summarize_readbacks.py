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
HOT_SITE_PATTERN = re.compile(
    r"(0x[0-9a-f]+)@(0x[0-9a-f]+):([0-9]+)\(w([0-9]+)\)", re.IGNORECASE
)
BUFFER_CONTRIBUTION_PATTERN = re.compile(
    r"(0x[0-9a-f]+)\+([0-9]+):([0-9]+)r/([0-9]+)w/([0-9]+)d/"
    r"([0-9]+)c/([0-9]+)b/([0-9]+(?:\.[0-9]+)?)ms",
    re.IGNORECASE,
)
TOP_CONTEXT_PATTERN = re.compile(
    r"(0x[0-9a-f]+)@(0x[0-9a-f]+):([0-9]+)\(w([0-9]+)\);"
    r"rax:(0x[0-9a-f]+);rcx:(0x[0-9a-f]+);rdx:(0x[0-9a-f]+);"
    r"rsi:(0x[0-9a-f]+);rdi:(0x[0-9a-f]+);rbp:(0x[0-9a-f]+);"
    r"rsp:(0x[0-9a-f]+);rcx_range:(0x[0-9a-f]+)-(0x[0-9a-f]+);"
    r"rdx_range:(0x[0-9a-f]+)-(0x[0-9a-f]+)",
    re.IGNORECASE,
)
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
    "site_window_kib",
    "site_window_hits",
    "discard_probe_hits",
    "discard_probe_valid",
    "discard_write_span_bytes",
    "discard_page_write_bytes",
    "discard_dirty_bytes",
    "discard_covered_bytes",
    "discard_full_requests",
    "discard_zero_dirty_requests",
    "tracked_buffers",
    "buffer_table_drops",
    "phase_split",
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
        fields.setdefault("site_window_kib", 0)
        fields.setdefault("site_window_hits", 0)
        fields.setdefault("phase_split", 0)
        fields.setdefault("prior_wait_total_ms", 0.0)
        fields.setdefault("current_wait_total_ms", fields.get("wait_total_ms", 0.0))
        for name in (
            "discard_probe_hits",
            "discard_probe_valid",
            "discard_write_span_bytes",
            "discard_page_write_bytes",
            "discard_dirty_bytes",
            "discard_covered_bytes",
            "discard_full_requests",
            "discard_zero_dirty_requests",
            "tracked_buffers",
            "buffer_table_drops",
        ):
            fields.setdefault(name, 0)
        for field_name in ("hot_buffers", "slow_buffers"):
            buffer_match = re.search(rf"\b{field_name}=\[([^]]*)\]", body)
            fields[field_name] = [
                {
                    "address": address.lower(),
                    "size_bytes": int(size_bytes),
                    "requests": int(requests),
                    "writes": int(writes),
                    "download_calls": int(download_calls),
                    "copies": int(copies),
                    "downloaded_bytes": int(downloaded_bytes),
                    "finish_ms": float(finish_ms),
                }
                for (
                    address,
                    size_bytes,
                    requests,
                    writes,
                    download_calls,
                    copies,
                    downloaded_bytes,
                    finish_ms,
                ) in BUFFER_CONTRIBUTION_PATTERN.findall(
                    buffer_match.group(1) if buffer_match else ""
                )
                if int(requests) != 0
            ]
        hot_match = re.search(r"\bhot=\[([^]]*)\]", body)
        fields["hot"] = [
            {"address": address.lower(), "requests": int(requests), "writes": int(writes)}
            for address, requests, writes in HOT_PATTERN.findall(
                hot_match.group(1) if hot_match else ""
            )
        ]
        hot_sites_match = re.search(r"\bhot_sites=\[([^]]*)\]", body)
        fields["hot_sites"] = [
            {
                "pc": pc.lower(),
                "address": address.lower(),
                "requests": int(requests),
                "writes": int(writes),
            }
            for pc, address, requests, writes in HOT_SITE_PATTERN.findall(
                hot_sites_match.group(1) if hot_sites_match else ""
            )
            if int(requests) != 0
        ]
        top_context_match = re.search(r"\btop_context=\[([^]]*)\]", body)
        parsed_contexts = TOP_CONTEXT_PATTERN.findall(
            top_context_match.group(1) if top_context_match else ""
        )
        fields["top_context"] = None
        if parsed_contexts and int(parsed_contexts[0][2]) != 0:
            (
                pc,
                address,
                requests,
                writes,
                rax,
                rcx,
                rdx,
                rsi,
                rdi,
                rbp,
                rsp,
                rcx_min,
                rcx_max,
                rdx_min,
                rdx_max,
            ) = parsed_contexts[0]
            fields["top_context"] = {
                "pc": pc.lower(),
                "address": address.lower(),
                "requests": int(requests),
                "writes": int(writes),
                "rax": int(rax, 16),
                "rcx": int(rcx, 16),
                "rdx": int(rdx, 16),
                "rsi": int(rsi, 16),
                "rdi": int(rdi, 16),
                "rbp": int(rbp, 16),
                "rsp": int(rsp, 16),
                "rcx_min": int(rcx_min, 16),
                "rcx_max": int(rcx_max, 16),
                "rdx_min": int(rdx_min, 16),
                "rdx_max": int(rdx_max, 16),
            }
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
            "site_window_hits",
            "discard_probe_hits",
            "discard_probe_valid",
            "discard_write_span_bytes",
            "discard_page_write_bytes",
            "discard_dirty_bytes",
            "discard_covered_bytes",
            "discard_full_requests",
            "discard_zero_dirty_requests",
            "tracked_buffers",
            "buffer_table_drops",
        )
    }
    finish_total_ms = sum(float(interval["finish_total_ms"]) for interval in selected)
    finish_max_ms = max(float(interval["finish_max_ms"]) for interval in selected)
    prior_wait_total_ms = sum(float(interval["prior_wait_total_ms"]) for interval in selected)
    current_wait_total_ms = sum(float(interval["current_wait_total_ms"]) for interval in selected)
    wall_total_ms = sum(float(interval.get("wall_ms", 0.0)) for interval in selected)
    requested_bytes = totals["requested_bytes"]
    requests = totals["requests"]
    site_windows = sorted(
        {int(interval["site_window_kib"]) for interval in selected if interval["site_window_kib"]}
    )
    dirty_probe_requests = (
        totals["discard_probe_valid"] - totals["discard_zero_dirty_requests"]
    )
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
    hot_sites: dict[tuple[str, str], dict[str, object]] = {}
    for interval in selected:
        for hot in interval["hot_sites"]:
            key = (hot["pc"], hot["address"])
            site = hot_sites.setdefault(
                key, {"pc": hot["pc"], "address": hot["address"], "requests": 0, "writes": 0}
            )
            site["requests"] += hot["requests"]
            site["writes"] += hot["writes"]
    hottest_sites = sorted(
        hot_sites.values(), key=lambda site: (-site["requests"], site["pc"], site["address"])
    )[:5]
    hot_contexts: dict[tuple[str, str], dict[str, object]] = {}
    for interval in selected:
        context = interval["top_context"]
        if context is None:
            continue
        key = (context["pc"], context["address"])
        combined = hot_contexts.setdefault(
            key,
            {
                "pc": context["pc"],
                "address": context["address"],
                "intervals": 0,
                "requests": 0,
                "writes": 0,
                "rcx_min": context["rcx_min"],
                "rcx_max": context["rcx_max"],
                "rdx_min": context["rdx_min"],
                "rdx_max": context["rdx_max"],
            },
        )
        combined["intervals"] += 1
        combined["requests"] += context["requests"]
        combined["writes"] += context["writes"]
        combined["rcx_min"] = min(combined["rcx_min"], context["rcx_min"])
        combined["rcx_max"] = max(combined["rcx_max"], context["rcx_max"])
        combined["rdx_min"] = min(combined["rdx_min"], context["rdx_min"])
        combined["rdx_max"] = max(combined["rdx_max"], context["rdx_max"])
        for register in ("rax", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp"):
            combined[register] = context[register]
    hottest_contexts = sorted(
        hot_contexts.values(),
        key=lambda context: (-context["requests"], context["pc"], context["address"]),
    )[:5]
    aggregated_buffers: dict[tuple[str, int], dict[str, object]] = {}
    for interval in selected:
        for field_name in ("hot_buffers", "slow_buffers"):
            for buffer in interval[field_name]:
                key = (buffer["address"], buffer["size_bytes"])
                contribution = aggregated_buffers.setdefault(
                    key,
                    {
                        "address": buffer["address"],
                        "size_bytes": buffer["size_bytes"],
                        "requests": 0,
                        "writes": 0,
                        "download_calls": 0,
                        "copies": 0,
                        "downloaded_bytes": 0,
                        "finish_ms": 0.0,
                        "intervals": set(),
                    },
                )
                interval_key = int(interval["line"])
                if interval_key in contribution["intervals"]:
                    continue
                contribution["intervals"].add(interval_key)
                for name in (
                    "requests",
                    "writes",
                    "download_calls",
                    "copies",
                    "downloaded_bytes",
                ):
                    contribution[name] += buffer[name]
                contribution["finish_ms"] += buffer["finish_ms"]
    for contribution in aggregated_buffers.values():
        contribution["intervals"] = len(contribution["intervals"])
        contribution["finish_ms"] = round(contribution["finish_ms"], 3)
    hottest_buffers = sorted(
        aggregated_buffers.values(),
        key=lambda buffer: (-buffer["downloaded_bytes"], buffer["address"], buffer["size_bytes"]),
    )[:5]
    slowest_buffers = sorted(
        aggregated_buffers.values(),
        key=lambda buffer: (-buffer["finish_ms"], buffer["address"], buffer["size_bytes"]),
    )[:5]
    return {
        "intervals_available": len(intervals),
        "intervals_selected": len(selected),
        "tail_count": tail_count,
        "window_kib": windows[0] if len(windows) == 1 else windows,
        "site_window_kib": site_windows[0] if len(site_windows) == 1 else site_windows,
        **totals,
        "finish_total_ms": round(finish_total_ms, 3),
        "finish_avg_ms_per_request": round(finish_total_ms / requests, 6) if requests else 0.0,
        "finish_max_ms": round(finish_max_ms, 3),
        "phase_split": any(bool(interval["phase_split"]) for interval in selected),
        "prior_wait_total_ms": round(prior_wait_total_ms, 3),
        "current_wait_total_ms": round(current_wait_total_ms, 3),
        "prior_wait_share_pct": round(prior_wait_total_ms * 100.0 / finish_total_ms, 3)
        if finish_total_ms
        else 0.0,
        "current_wait_share_pct": round(current_wait_total_ms * 100.0 / finish_total_ms, 3)
        if finish_total_ms
        else 0.0,
        "wall_total_ms": round(wall_total_ms, 3) if wall_total_ms else None,
        "request_rate": round(requests * 1000.0 / wall_total_ms, 3) if wall_total_ms else None,
        "finish_share_pct": round(finish_total_ms * 100.0 / wall_total_ms, 3)
        if wall_total_ms
        else None,
        "amplification": round(totals["downloaded_bytes"] / requested_bytes, 3)
        if requested_bytes
        else 0.0,
        "copies_per_request": round(totals["copies"] / requests, 3) if requests else 0.0,
        "discard_valid_pct": round(
            totals["discard_probe_valid"] * 100.0 / totals["discard_probe_hits"], 3
        )
        if totals["discard_probe_hits"]
        else 0.0,
        "discard_dirty_coverage_pct": round(
            totals["discard_covered_bytes"] * 100.0 / totals["discard_dirty_bytes"], 3
        )
        if totals["discard_dirty_bytes"]
        else 0.0,
        "discard_full_request_pct": round(
            totals["discard_full_requests"] * 100.0 / dirty_probe_requests, 3
        )
        if dirty_probe_requests
        else 0.0,
        "downloaded_bytes_per_request": round(totals["downloaded_bytes"] / requests, 3)
        if requests
        else 0.0,
        "hottest_pages": hottest,
        "hottest_sites": hottest_sites,
        "hottest_contexts": hottest_contexts,
        "hottest_buffers": hottest_buffers,
        "slowest_buffers": slowest_buffers,
    }


def render_text(log_path: Path, result: dict[str, object]) -> str:
    window = result["window_kib"]
    window_text = str(window) if isinstance(window, int) else ",".join(map(str, window))
    lines = [
        f"log={log_path}",
        f"intervals={result['intervals_selected']}/{result['intervals_available']}",
        f"window_kib={window_text}",
        "site_window_kib={} site_window_hits={}".format(
            result["site_window_kib"] or 0, result["site_window_hits"]
        ),
        "discard_probe_hits={} discard_probe_valid={} discard_write_span_bytes={} "
        "discard_page_write_bytes={}".format(
            result["discard_probe_hits"],
            result["discard_probe_valid"],
            result["discard_write_span_bytes"],
            result["discard_page_write_bytes"],
        ),
        "discard_dirty_bytes={} discard_covered_bytes={} discard_full_requests={} "
        "discard_zero_dirty_requests={} valid_pct={} dirty_coverage_pct={} "
        "full_request_pct={}".format(
            result["discard_dirty_bytes"],
            result["discard_covered_bytes"],
            result["discard_full_requests"],
            result["discard_zero_dirty_requests"],
            result["discard_valid_pct"],
            result["discard_dirty_coverage_pct"],
            result["discard_full_request_pct"],
        ),
        "tracked_buffer_slots={} buffer_table_drops={}".format(
            result["tracked_buffers"], result["buffer_table_drops"]
        ),
        f"requests={result['requests']} writes={result['writes']} reads={result['reads']}",
        f"requested_bytes={result['requested_bytes']} downloaded_bytes={result['downloaded_bytes']}",
        f"amplification={result['amplification']}x copies_per_request={result['copies_per_request']}",
        "finish_total_ms={} finish_avg_ms_per_request={} finish_max_ms={}".format(
            result["finish_total_ms"],
            result["finish_avg_ms_per_request"],
            result["finish_max_ms"],
        ),
        "phase_split={} prior_wait_total_ms={} current_wait_total_ms={} "
        "prior_wait_share_pct={} current_wait_share_pct={}".format(
            int(result["phase_split"]),
            result["prior_wait_total_ms"],
            result["current_wait_total_ms"],
            result["prior_wait_share_pct"],
            result["current_wait_share_pct"],
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
    if result["hottest_sites"]:
        hot_sites = ", ".join(
            f"{site['pc']}@{site['address']}:{site['requests']}(w{site['writes']})"
            for site in result["hottest_sites"]
        )
        lines.append(f"hottest_sites={hot_sites}")
    if result["hottest_contexts"]:
        contexts = ", ".join(
            "{}@{}:{}i/{}r(w{}) rcx={}-{} rdx={}-{} last_rdi={} last_rsi={}".format(
                context["pc"],
                context["address"],
                context["intervals"],
                context["requests"],
                context["writes"],
                hex(context["rcx_min"]),
                hex(context["rcx_max"]),
                hex(context["rdx_min"]),
                hex(context["rdx_max"]),
                hex(context["rdi"]),
                hex(context["rsi"]),
            )
            for context in result["hottest_contexts"]
        )
        lines.append(f"hottest_contexts={contexts}")
    if result["hottest_buffers"]:
        buffers = ", ".join(
            "{}+{}:{}i/{}r/{}w/{}d/{}c/{}b/{}ms".format(
                buffer["address"],
                buffer["size_bytes"],
                buffer["intervals"],
                buffer["requests"],
                buffer["writes"],
                buffer["download_calls"],
                buffer["copies"],
                buffer["downloaded_bytes"],
                buffer["finish_ms"],
            )
            for buffer in result["hottest_buffers"]
        )
        lines.append(f"hottest_buffers={buffers}")
    if result["slowest_buffers"]:
        buffers = ", ".join(
            "{}+{}:{}i/{}r/{}w/{}d/{}c/{}b/{}ms".format(
                buffer["address"],
                buffer["size_bytes"],
                buffer["intervals"],
                buffer["requests"],
                buffer["writes"],
                buffer["download_calls"],
                buffer["copies"],
                buffer["downloaded_bytes"],
                buffer["finish_ms"],
            )
            for buffer in result["slowest_buffers"]
        )
        lines.append(f"slowest_buffers={buffers}")
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
