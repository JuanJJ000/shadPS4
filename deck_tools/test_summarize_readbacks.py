#!/usr/bin/env python3

import unittest

import summarize_readbacks


class SummarizeReadbacksTest(unittest.TestCase):
    def test_weighted_totals_and_hot_pages(self):
        text = """
[Render.Vulkan] <Info> Precise readback stats: window_kib=64 requests=2 writes=1 reads=1 bounded_repeats=1 tracked_pages=2 requested_bytes=16 download_calls=2 copies=6 downloaded_bytes=4096 no_downloads=0 finish_total_ms=3.000 finish_avg_ms=1.500 finish_max_ms=2.000 amplification=256.0x hot=[0x1000:2(w1), 0x2000:1(w0), 0x0:0(w0)]
[Render.Vulkan] <Info> Precise readback stats: window_kib=64 requests=2 writes=0 reads=2 bounded_repeats=2 tracked_pages=1 requested_bytes=16 download_calls=1 copies=2 downloaded_bytes=2048 no_downloads=1 finish_total_ms=1.000 finish_avg_ms=0.500 finish_max_ms=1.000 amplification=128.0x hot=[0x1000:2(w0), 0x0:0(w0), 0x0:0(w0)]
"""
        intervals = summarize_readbacks.parse_intervals(text)
        result = summarize_readbacks.summarize(intervals, 0)
        self.assertEqual(result["window_kib"], 64)
        self.assertEqual(result["requests"], 4)
        self.assertEqual(result["downloaded_bytes"], 6144)
        self.assertEqual(result["amplification"], 192.0)
        self.assertEqual(result["finish_avg_ms_per_request"], 1.0)
        self.assertEqual(result["hottest_pages"][0]["address"], "0x1000")
        self.assertEqual(result["hottest_pages"][0]["requests"], 4)

    def test_legacy_line_defaults_to_512_kib(self):
        text = "Precise readback stats: requests=1 writes=1 reads=0 bounded_repeats=0 " \
            "tracked_pages=1 requested_bytes=8 download_calls=1 copies=1 downloaded_bytes=64 " \
            "no_downloads=0 finish_total_ms=0.500 finish_avg_ms=0.500 finish_max_ms=0.500 " \
            "amplification=8.0x hot=[0x1000:1(w1), 0x0:0(w0), 0x0:0(w0)]"
        intervals = summarize_readbacks.parse_intervals(text)
        self.assertEqual(intervals[0]["window_kib"], 512)


if __name__ == "__main__":
    unittest.main()
