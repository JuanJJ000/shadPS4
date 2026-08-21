#!/usr/bin/env python3

import unittest

import summarize_readbacks


class SummarizeReadbacksTest(unittest.TestCase):
    def test_weighted_totals_and_hot_pages(self):
        text = """
[Render.Vulkan] <Info> Precise readback stats: window_kib=64 requests=2 writes=1 reads=1 bounded_repeats=1 tracked_pages=2 requested_bytes=16 download_calls=2 copies=6 downloaded_bytes=4096 no_downloads=0 finish_total_ms=3.000 finish_avg_ms=1.500 finish_max_ms=2.000 wall_ms=20.000 request_rate=100.0 finish_share_pct=15.0 amplification=256.0x hot=[0x1000:2(w1), 0x2000:1(w0), 0x0:0(w0)] hot_sites=[0xaaa@0x1000:2(w1), 0x0@0x0:0(w0), 0x0@0x0:0(w0)] top_context=[0xaaa@0x1000:2(w1);rax:0x10;rcx:0x20;rdx:0x40;rsi:0x2000;rdi:0x1000;rbp:0x3000;rsp:0x2ff0;rcx_range:0x18-0x20;rdx_range:0x40-0x40]
[Render.Vulkan] <Info> Precise readback stats: window_kib=64 requests=2 writes=0 reads=2 bounded_repeats=2 tracked_pages=1 requested_bytes=16 download_calls=1 copies=2 downloaded_bytes=2048 no_downloads=1 finish_total_ms=1.000 finish_avg_ms=0.500 finish_max_ms=1.000 wall_ms=20.000 request_rate=100.0 finish_share_pct=5.0 amplification=128.0x hot=[0x1000:2(w0), 0x0:0(w0), 0x0:0(w0)] hot_sites=[0xaaa@0x1000:1(w0), 0xbbb@0x1000:1(w0), 0x0@0x0:0(w0)] top_context=[0xaaa@0x1000:1(w0);rax:0x10;rcx:0x10;rdx:0x80;rsi:0x2100;rdi:0x1100;rbp:0x3000;rsp:0x2ff0;rcx_range:0x10-0x10;rdx_range:0x80-0x80]
"""
        intervals = summarize_readbacks.parse_intervals(text)
        result = summarize_readbacks.summarize(intervals, 0)
        self.assertEqual(result["window_kib"], 64)
        self.assertEqual(result["requests"], 4)
        self.assertEqual(result["downloaded_bytes"], 6144)
        self.assertEqual(result["amplification"], 192.0)
        self.assertEqual(result["finish_avg_ms_per_request"], 1.0)
        self.assertEqual(result["request_rate"], 100.0)
        self.assertEqual(result["finish_share_pct"], 10.0)
        self.assertEqual(result["hottest_pages"][0]["address"], "0x1000")
        self.assertEqual(result["hottest_pages"][0]["requests"], 4)
        self.assertEqual(result["hottest_sites"][0]["pc"], "0xaaa")
        self.assertEqual(result["hottest_sites"][0]["requests"], 3)
        self.assertEqual(result["hottest_contexts"][0]["pc"], "0xaaa")
        self.assertEqual(result["hottest_contexts"][0]["intervals"], 2)
        self.assertEqual(result["hottest_contexts"][0]["requests"], 3)
        self.assertEqual(result["hottest_contexts"][0]["rdx_min"], 0x40)
        self.assertEqual(result["hottest_contexts"][0]["rdx_max"], 0x80)

    def test_legacy_line_defaults_to_512_kib(self):
        text = "Precise readback stats: requests=1 writes=1 reads=0 bounded_repeats=0 " \
            "tracked_pages=1 requested_bytes=8 download_calls=1 copies=1 downloaded_bytes=64 " \
            "no_downloads=0 finish_total_ms=0.500 finish_avg_ms=0.500 finish_max_ms=0.500 " \
            "amplification=8.0x hot=[0x1000:1(w1), 0x0:0(w0), 0x0:0(w0)]"
        intervals = summarize_readbacks.parse_intervals(text)
        self.assertEqual(intervals[0]["window_kib"], 512)


if __name__ == "__main__":
    unittest.main()
