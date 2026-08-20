#!/usr/bin/env python3
"""Summarize MangoHud's whitespace-delimited benchmark logs without third-party modules."""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path


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


def read_log(path: Path) -> dict[str, list[float]] | None:
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

    columns: dict[str, list[float]] = {header: [] for header in headers}
    for line in lines[header_index + 1 :]:
        fields = [field for field in re.split(r"[\s,]+", line.strip()) if field]
        if len(fields) < len(headers):
            continue
        for header, value in zip(headers, fields):
            try:
                number = float(value)
            except ValueError:
                continue
            if math.isfinite(number):
                columns[header].append(number)
    return columns


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    logs: list[tuple[Path, dict[str, list[float]]]] = []
    for path in sorted(args.run_dir.rglob("*.csv")):
        columns = read_log(path)
        if columns is not None:
            logs.append((path, columns))

    print("MangoHud performance summary")
    print("Raw non-FPS units are preserved exactly as recorded by MangoHud.")
    if not logs:
        print("No MangoHud benchmark CSV with an fps column was found.")
        return 1

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
        "elapsed",
    ]
    for path, columns in logs:
        print(f"\nlog: {path.relative_to(args.run_dir)}")
        fps = columns.get("fps", [])
        print(f"samples: {len(fps)}")
        if fps:
            print(
                "fps: "
                f"mean={format_value(sum(fps) / len(fps))}, "
                f"median={format_value(percentile(fps, 50))}, "
                f"1% low={format_value(percentile(fps, 1))}, "
                f"0.1% low={format_value(percentile(fps, 0.1))}, "
                f"min={format_value(min(fps))}, max={format_value(max(fps))}"
            )
        frametime = columns.get("frametime", [])
        if frametime:
            print(
                "frametime: "
                f"mean={format_value(sum(frametime) / len(frametime))}, "
                f"median={format_value(percentile(frametime, 50))}, "
                f"p95={format_value(percentile(frametime, 95))}, "
                f"p99={format_value(percentile(frametime, 99))}, "
                f"max={format_value(max(frametime))}"
            )
        for name in preferred:
            for line in summarize_column(name, columns.get(name, [])):
                print(line)

        extra = sorted(set(columns) - {"fps", "frametime", *preferred})
        if extra:
            print(f"other columns: {', '.join(extra)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
