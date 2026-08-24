#!/usr/bin/env python3
"""Summarize MangoHud benchmark logs, including bounded time phases."""

from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path


ELAPSED_NANOSECONDS_PER_SECOND = 1_000_000_000.0


@dataclass(frozen=True)
class Phase:
    label: str
    start_seconds: float
    end_seconds: float | None


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    index = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def read_log(path: Path) -> tuple[list[str], list[dict[str, float]]] | None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_index = -1
    headers: list[str] = []
    for index, line in enumerate(lines[:101]):
        fields = [field for field in re.split(r"[\s,]+", line.strip()) if field]
        if "fps" in fields:
            header_index = index
            headers = fields
    if header_index < 0:
        return None

    rows: list[dict[str, float]] = []
    for line in lines[header_index + 1 :]:
        fields = [field for field in re.split(r"[\s,]+", line.strip()) if field]
        if len(fields) < len(headers):
            continue
        row: dict[str, float] = {}
        for header, value in zip(headers, fields):
            try:
                number = float(value)
            except ValueError:
                continue
            if math.isfinite(number):
                row[header] = number
        if "fps" in row:
            rows.append(row)
    return headers, rows


def columns_for_rows(headers: list[str], rows: list[dict[str, float]]) -> dict[str, list[float]]:
    return {header: [row[header] for row in rows if header in row] for header in headers}


def elapsed_seconds(row: dict[str, float]) -> float | None:
    elapsed = row.get("elapsed")
    if elapsed is None:
        return None
    return elapsed / ELAPSED_NANOSECONDS_PER_SECOND


def filter_elapsed(
    rows: list[dict[str, float]], start_seconds: float, end_seconds: float | None
) -> list[dict[str, float]]:
    selected = []
    for row in rows:
        elapsed = elapsed_seconds(row)
        if elapsed is None or elapsed < start_seconds:
            continue
        if end_seconds is not None and elapsed >= end_seconds:
            continue
        selected.append(row)
    return selected


def positive_seconds(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a number of seconds") from error
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("seconds must be a positive finite number")
    return number


def parse_phase(value: str) -> Phase:
    try:
        label, bounds = value.split("=", 1)
        start_text, end_text = bounds.split(":", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("phase must use LABEL=START:END") from error
    label = label.strip()
    if not label or not start_text:
        raise argparse.ArgumentTypeError("phase label and start time are required")
    try:
        start = float(start_text)
        end = float(end_text) if end_text else None
    except ValueError as error:
        raise argparse.ArgumentTypeError("phase bounds must be numbers of seconds") from error
    if not math.isfinite(start) or start < 0:
        raise argparse.ArgumentTypeError("phase start must be a non-negative finite number")
    if end is not None and (not math.isfinite(end) or end <= start):
        raise argparse.ArgumentTypeError("phase end must be greater than its start")
    return Phase(label, start, end)


def format_value(value: float) -> str:
    return "n/a" if math.isnan(value) else f"{value:.2f}"


def summarize_column(name: str, values: list[float]) -> list[str]:
    if not values:
        return []
    average = sum(values) / len(values)
    return [
        f"{name}: mean={format_value(average)}, median={format_value(percentile(values, 50))}, "
        f"p95={format_value(percentile(values, 95))}, max={format_value(max(values))}"
    ]


def summary_lines(headers: list[str], rows: list[dict[str, float]], compact: bool = False) -> list[str]:
    columns = columns_for_rows(headers, rows)
    lines = [f"samples: {len(columns.get('fps', []))}"]
    fps = columns.get("fps", [])
    if fps:
        lines.append(
            "fps: "
            f"mean={format_value(sum(fps) / len(fps))}, "
            f"median={format_value(percentile(fps, 50))}, "
            f"1% low={format_value(percentile(fps, 1))}, "
            f"0.1% low={format_value(percentile(fps, 0.1))}, "
            f"min={format_value(min(fps))}, max={format_value(max(fps))}"
        )
    frametime = columns.get("frametime", [])
    if frametime:
        lines.append(
            "frametime: "
            f"mean={format_value(sum(frametime) / len(frametime))}, "
            f"median={format_value(percentile(frametime, 50))}, "
            f"p95={format_value(percentile(frametime, 95))}, "
            f"p99={format_value(percentile(frametime, 99))}, "
            f"max={format_value(max(frametime))}"
        )
    if compact:
        gpu_load = columns.get("gpu_load", [])
        if gpu_load:
            lines.append(f"gpu_load: mean={format_value(sum(gpu_load) / len(gpu_load))}")
        return lines

    preferred = [
        "cpu_load",
        "gpu_load",
        "cpu_temp",
        "gpu_temp",
        "gpu_core_clock",
        "gpu_mem_clock",
        "gpu_vram_used",
        "gpu_power",
        "ram_used",
        "swap_used",
        "process_rss",
    ]
    for name in preferred:
        lines.extend(summarize_column(name, columns.get(name, [])))

    extra = sorted(set(columns) - {"fps", "frametime", "elapsed", *preferred})
    if extra:
        lines.append(f"other columns: {', '.join(extra)}")
    return lines


def phase_heading(phase: Phase) -> str:
    if phase.end_seconds is None:
        return f"phase {phase.label}: {phase.start_seconds:.2f}s to end"
    return f"phase {phase.label}: {phase.start_seconds:.2f}s to {phase.end_seconds:.2f}s"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--phase",
        action="append",
        default=[],
        type=parse_phase,
        metavar="LABEL=START:END",
        help="summarize a named elapsed-time range in seconds; END may be empty",
    )
    parser.add_argument(
        "--tail-seconds",
        type=positive_seconds,
        help="summarize the final elapsed-time window",
    )
    parser.add_argument(
        "--bin-seconds",
        type=positive_seconds,
        help="emit a compact elapsed-time timeline",
    )
    args = parser.parse_args()

    logs: list[tuple[Path, list[str], list[dict[str, float]]]] = []
    for path in sorted(args.run_dir.rglob("*.csv")):
        parsed = read_log(path)
        if parsed is not None:
            headers, rows = parsed
            logs.append((path, headers, rows))

    print("MangoHud performance summary")
    print("Elapsed phases are seconds; other non-FPS units are preserved as MangoHud recorded them.")
    if not logs:
        print("No MangoHud benchmark CSV with an fps column was found.")
        return 1

    for path, headers, rows in logs:
        print(f"\nlog: {path.relative_to(args.run_dir)}")
        elapsed_values = [elapsed for row in rows if (elapsed := elapsed_seconds(row)) is not None]
        duration = max(elapsed_values, default=0.0)
        print(f"duration_seconds: {duration:.2f}")
        for line in summary_lines(headers, rows):
            print(line)

        for phase in args.phase:
            print(f"\n{phase_heading(phase)}")
            for line in summary_lines(
                headers, filter_elapsed(rows, phase.start_seconds, phase.end_seconds), compact=True
            ):
                print(line)

        if args.tail_seconds is not None:
            start = max(0.0, duration - args.tail_seconds)
            print(f"\ntail: final {args.tail_seconds:.2f}s ({start:.2f}s to {duration:.2f}s)")
            for line in summary_lines(headers, filter_elapsed(rows, start, None), compact=True):
                print(line)

        if args.bin_seconds is not None:
            print(f"\ntimeline: {args.bin_seconds:.2f}s bins")
            start = 0.0
            while start <= duration:
                end = start + args.bin_seconds
                selected = filter_elapsed(rows, start, end)
                if selected:
                    print(f"\nbin: {start:.2f}s to {min(end, duration):.2f}s")
                    for line in summary_lines(headers, selected, compact=True):
                        print(line)
                start = end
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
