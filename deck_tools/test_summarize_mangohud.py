#!/usr/bin/env python3

import argparse
import tempfile
import unittest
from pathlib import Path

if __package__:
    from . import summarize_mangohud
else:
    import summarize_mangohud


class SummarizeMangoHudTests(unittest.TestCase):
    def test_read_log_preserves_row_alignment_for_elapsed_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            path.write_text(
                "v1\n"
                "--------------------FRAME METRICS--------------------\n"
                "fps,frametime,gpu_load,elapsed\n"
                "60,16.67,90,1000000000\n"
                "invalid,20,40,2000000000\n"
                "30,33.33,50,3000000000\n",
                encoding="utf-8",
            )
            parsed = summarize_mangohud.read_log(path)

        self.assertIsNotNone(parsed)
        headers, rows = parsed
        self.assertEqual(headers, ["fps", "frametime", "gpu_load", "elapsed"])
        self.assertEqual(len(rows), 2)
        tail = summarize_mangohud.filter_elapsed(rows, 2.0, None)
        self.assertEqual([row["fps"] for row in tail], [30.0])
        self.assertEqual([row["gpu_load"] for row in tail], [50.0])

    def test_elapsed_filter_uses_half_open_ranges(self):
        rows = [
            {"fps": 60.0, "elapsed": 0.0},
            {"fps": 50.0, "elapsed": 5_000_000_000.0},
            {"fps": 40.0, "elapsed": 10_000_000_000.0},
            {"fps": 30.0},
        ]
        selected = summarize_mangohud.filter_elapsed(rows, 5.0, 10.0)
        self.assertEqual([row["fps"] for row in selected], [50.0])

    def test_parse_phase_accepts_open_end(self):
        phase = summarize_mangohud.parse_phase("post-load=15:")
        self.assertEqual(phase.label, "post-load")
        self.assertEqual(phase.start_seconds, 15.0)
        self.assertIsNone(phase.end_seconds)

    def test_parse_phase_rejects_reversed_bounds(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            summarize_mangohud.parse_phase("bad=10:5")

    def test_compact_summary_keeps_fps_frametime_and_gpu_load(self):
        headers = ["fps", "frametime", "gpu_load", "elapsed"]
        rows = [
            {"fps": 60.0, "frametime": 16.0, "gpu_load": 90.0, "elapsed": 0.0},
            {"fps": 30.0, "frametime": 32.0, "gpu_load": 50.0, "elapsed": 1.0},
        ]
        output = "\n".join(summarize_mangohud.summary_lines(headers, rows, compact=True))
        self.assertIn("samples: 2", output)
        self.assertIn("fps: mean=45.00", output)
        self.assertIn("frametime: mean=24.00", output)
        self.assertIn("gpu_load: mean=70.00", output)


if __name__ == "__main__":
    unittest.main()
