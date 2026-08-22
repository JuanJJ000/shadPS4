#!/usr/bin/env python3

import unittest

import summarize_readbacks


class SummarizeReadbacksTest(unittest.TestCase):
    def test_weighted_totals_and_hot_pages(self):
        text = """
[Render.Vulkan] <Info> Precise readback stats: window_kib=64 requests=2 writes=1 reads=1 bounded_repeats=1 tracked_pages=2 requested_bytes=16 download_calls=2 copies=6 downloaded_bytes=4096 no_downloads=0 finish_total_ms=3.000 finish_avg_ms=1.500 finish_max_ms=2.000 wall_ms=20.000 request_rate=100.0 finish_share_pct=15.0 site_window_kib=16 site_window_hits=1 amplification=256.0x hot=[0x1000:2(w1), 0x2000:1(w0), 0x0:0(w0)] hot_sites=[0xaaa@0x1000:2(w1), 0x0@0x0:0(w0), 0x0@0x0:0(w0)] top_context=[0xaaa@0x1000:2(w1);rax:0x10;rcx:0x20;rdx:0x40;rsi:0x2000;rdi:0x1000;rbp:0x3000;rsp:0x2ff0;rcx_range:0x18-0x20;rdx_range:0x40-0x40]
[Render.Vulkan] <Info> Precise readback stats: window_kib=64 requests=2 writes=0 reads=2 bounded_repeats=2 tracked_pages=1 requested_bytes=16 download_calls=1 copies=2 downloaded_bytes=2048 no_downloads=1 finish_total_ms=1.000 finish_avg_ms=0.500 finish_max_ms=1.000 wall_ms=20.000 request_rate=100.0 finish_share_pct=5.0 site_window_kib=16 site_window_hits=0 amplification=128.0x hot=[0x1000:2(w0), 0x0:0(w0), 0x0:0(w0)] hot_sites=[0xaaa@0x1000:1(w0), 0xbbb@0x1000:1(w0), 0x0@0x0:0(w0)] top_context=[0xaaa@0x1000:1(w0);rax:0x10;rcx:0x10;rdx:0x80;rsi:0x2100;rdi:0x1100;rbp:0x3000;rsp:0x2ff0;rcx_range:0x10-0x10;rdx_range:0x80-0x80]
"""
        text = text.replace(
            "amplification=256.0x",
            "phase_split=1 prior_wait_total_ms=2.000 current_wait_total_ms=0.800 "
            "gpu_timestamp=1 gpu_timestamp_bits=64 gpu_timestamp_period_ns=1.000 "
            "gpu_timestamp_samples=2 gpu_timestamp_failures=0 gpu_copy_total_ms=0.200 "
            "gpu_copy_share_current_pct=25.000 "
            "discard_probe_hits=2 discard_probe_valid=2 discard_write_span_bytes=8192 "
            "discard_page_write_bytes=4096 discard_dirty_bytes=2048 discard_covered_bytes=2048 "
            "discard_full_requests=1 discard_zero_dirty_requests=0 tracked_buffers=2 "
            "buffer_table_drops=0 "
            "hot_buffers=[0x3000+65536:2r/1w/2d/6c/4096b/3.000ms, "
            "0x4000+131072:1r/0w/1d/2c/2048b/1.000ms, "
            "0x0+0:0r/0w/0d/0c/0b/0.000ms] "
            "slow_buffers=[0x3000+65536:2r/1w/2d/6c/4096b/3.000ms, "
            "0x4000+131072:1r/0w/1d/2c/2048b/1.000ms, "
            "0x0+0:0r/0w/0d/0c/0b/0.000ms] amplification=256.0x",
        ).replace(
            "amplification=128.0x",
            "phase_split=1 prior_wait_total_ms=0.600 current_wait_total_ms=0.300 "
            "gpu_timestamp=1 gpu_timestamp_bits=64 gpu_timestamp_period_ns=1.000 "
            "gpu_timestamp_samples=1 gpu_timestamp_failures=0 gpu_copy_total_ms=0.100 "
            "gpu_copy_share_current_pct=33.333 "
            "discard_probe_hits=1 discard_probe_valid=1 discard_write_span_bytes=4096 "
            "discard_page_write_bytes=2048 discard_dirty_bytes=1024 discard_covered_bytes=512 "
            "discard_full_requests=0 discard_zero_dirty_requests=0 tracked_buffers=1 "
            "buffer_table_drops=0 "
            "hot_buffers=[0x3000+65536:2r/0w/1d/2c/2048b/1.000ms, "
            "0x0+0:0r/0w/0d/0c/0b/0.000ms, 0x0+0:0r/0w/0d/0c/0b/0.000ms] "
            "slow_buffers=[0x3000+65536:2r/0w/1d/2c/2048b/1.000ms, "
            "0x0+0:0r/0w/0d/0c/0b/0.000ms, 0x0+0:0r/0w/0d/0c/0b/0.000ms] "
            "amplification=128.0x",
        )
        intervals = summarize_readbacks.parse_intervals(text)
        result = summarize_readbacks.summarize(intervals, 0)
        self.assertEqual(result["window_kib"], 64)
        self.assertEqual(result["requests"], 4)
        self.assertEqual(result["downloaded_bytes"], 6144)
        self.assertEqual(result["amplification"], 192.0)
        self.assertEqual(result["finish_avg_ms_per_request"], 1.0)
        self.assertEqual(result["request_rate"], 100.0)
        self.assertEqual(result["finish_share_pct"], 10.0)
        self.assertTrue(result["phase_split"])
        self.assertEqual(result["prior_wait_total_ms"], 2.6)
        self.assertEqual(result["current_wait_total_ms"], 1.1)
        self.assertEqual(result["prior_wait_share_pct"], 65.0)
        self.assertEqual(result["current_wait_share_pct"], 27.5)
        self.assertTrue(result["gpu_timestamp"])
        self.assertEqual(result["gpu_timestamp_bits"], [64])
        self.assertEqual(result["gpu_timestamp_period_ns"], [1.0])
        self.assertEqual(result["gpu_timestamp_samples"], 3)
        self.assertEqual(result["gpu_timestamp_failures"], 0)
        self.assertEqual(result["gpu_copy_total_ms"], 0.3)
        self.assertEqual(result["gpu_copy_share_current_pct"], 27.273)
        self.assertEqual(result["hottest_pages"][0]["address"], "0x1000")
        self.assertEqual(result["hottest_pages"][0]["requests"], 4)
        self.assertEqual(result["hottest_sites"][0]["pc"], "0xaaa")
        self.assertEqual(result["hottest_sites"][0]["requests"], 3)
        self.assertEqual(result["hottest_contexts"][0]["pc"], "0xaaa")
        self.assertEqual(result["hottest_contexts"][0]["intervals"], 2)
        self.assertEqual(result["hottest_contexts"][0]["requests"], 3)
        self.assertEqual(result["hottest_contexts"][0]["rdx_min"], 0x40)
        self.assertEqual(result["hottest_contexts"][0]["rdx_max"], 0x80)
        self.assertEqual(result["site_window_kib"], 16)
        self.assertEqual(result["site_window_hits"], 1)
        self.assertEqual(result["discard_probe_hits"], 3)
        self.assertEqual(result["discard_probe_valid"], 3)
        self.assertEqual(result["discard_dirty_bytes"], 3072)
        self.assertEqual(result["discard_covered_bytes"], 2560)
        self.assertEqual(result["discard_full_requests"], 1)
        self.assertEqual(result["discard_valid_pct"], 100.0)
        self.assertEqual(result["discard_dirty_coverage_pct"], 83.333)
        self.assertEqual(result["discard_full_request_pct"], 33.333)
        self.assertEqual(result["tracked_buffers"], 3)
        self.assertEqual(result["buffer_table_drops"], 0)
        self.assertEqual(result["hottest_buffers"][0]["address"], "0x3000")
        self.assertEqual(result["hottest_buffers"][0]["intervals"], 2)
        self.assertEqual(result["hottest_buffers"][0]["downloaded_bytes"], 6144)
        self.assertEqual(result["hottest_buffers"][0]["finish_ms"], 4.0)
        self.assertEqual(result["slowest_buffers"][0]["address"], "0x3000")

    def test_legacy_line_defaults_to_512_kib(self):
        text = "Precise readback stats: requests=1 writes=1 reads=0 bounded_repeats=0 " \
            "tracked_pages=1 requested_bytes=8 download_calls=1 copies=1 downloaded_bytes=64 " \
            "no_downloads=0 finish_total_ms=0.500 finish_avg_ms=0.500 finish_max_ms=0.500 " \
            "amplification=8.0x hot=[0x1000:1(w1), 0x0:0(w0), 0x0:0(w0)]"
        intervals = summarize_readbacks.parse_intervals(text)
        self.assertEqual(intervals[0]["window_kib"], 512)
        self.assertEqual(intervals[0]["site_window_kib"], 0)
        self.assertEqual(intervals[0]["site_window_hits"], 0)
        self.assertEqual(intervals[0]["phase_split"], 0)
        self.assertEqual(intervals[0]["prior_wait_total_ms"], 0.0)
        self.assertEqual(intervals[0]["current_wait_total_ms"], 0.0)
        self.assertEqual(intervals[0]["gpu_timestamp"], 0)
        self.assertEqual(intervals[0]["gpu_timestamp_bits"], 0)
        self.assertEqual(intervals[0]["gpu_timestamp_period_ns"], 0.0)
        self.assertEqual(intervals[0]["gpu_timestamp_samples"], 0)
        self.assertEqual(intervals[0]["gpu_timestamp_failures"], 0)
        self.assertEqual(intervals[0]["gpu_copy_total_ms"], 0.0)
        self.assertEqual(intervals[0]["discard_probe_hits"], 0)
        self.assertEqual(intervals[0]["discard_full_requests"], 0)
        self.assertEqual(intervals[0]["tracked_buffers"], 0)
        self.assertEqual(intervals[0]["buffer_table_drops"], 0)
        self.assertEqual(intervals[0]["hot_buffers"], [])
        self.assertEqual(intervals[0]["slow_buffers"], [])


if __name__ == "__main__":
    unittest.main()
