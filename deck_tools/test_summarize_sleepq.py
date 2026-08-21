#!/usr/bin/env python3

import unittest

import summarize_sleepq


class SummarizeSleepQueueTest(unittest.TestCase):
    def test_weighted_totals_classes_and_hot_bucket(self):
        text = """
[Kernel.Pthread] <Info> Sleep queue stats: acquisitions=10 contended=4 contention_pct=40.0 wchan_changes=1 wall_ms=20.000 acquisition_rate=500.0 wait_total_ms=8.000 wait_max_ms=3.000 timed_holds=5 hold_total_ms=2.000 hold_max_ms=1.000 off_cpu_total_ms=1.500 off_cpu_max_ms=0.900 off_cpu_holds_over_50us=2 wait_share_pct=40.0 sampled_off_cpu_share_pct=75.0 contention_owners=[unknown:0,main:1,workers:3,movie:0,other:0] contention_waiters=[unknown:0,main:2,workers:2,movie:0,other:0] off_cpu_owner_ms=[unknown:0.000,main:0.100,workers:1.400,movie:0.000,other:0.000] top=[298:8acq/4cont/1ch/0x1000wc/8.000wait_ms]
[Kernel.Pthread] <Info> Sleep queue stats: acquisitions=20 contended=2 contention_pct=10.0 wchan_changes=0 wall_ms=40.000 acquisition_rate=500.0 wait_total_ms=2.000 wait_max_ms=1.000 timed_holds=3 hold_total_ms=1.000 hold_max_ms=0.500 off_cpu_total_ms=0.500 off_cpu_max_ms=0.400 off_cpu_holds_over_50us=1 wait_share_pct=5.0 sampled_off_cpu_share_pct=50.0 contention_owners=[unknown:0,main:0,workers:2,movie:0,other:0] contention_waiters=[unknown:0,main:1,workers:1,movie:0,other:0] off_cpu_owner_ms=[unknown:0.000,main:0.000,workers:0.500,movie:0.000,other:0.000] top=[298:19acq/2cont/0ch/0x1000wc/2.000wait_ms]
"""
        intervals = summarize_sleepq.parse_intervals(text)
        result = summarize_sleepq.summarize(intervals, 0)
        self.assertEqual(result["acquisitions"], 30)
        self.assertEqual(result["contention_pct"], 20.0)
        self.assertEqual(result["wait_share_pct"], 16.667)
        self.assertEqual(result["sampled_off_cpu_share_pct"], 66.667)
        self.assertEqual(result["contention_owners"]["workers"], 5.0)
        self.assertEqual(result["reported_hot_buckets"][0]["bucket"], 298)
        self.assertEqual(result["reported_hot_buckets"][0]["wait_ms"], 10.0)

    def test_tail_selects_latest_interval(self):
        text = """
Sleep queue stats: acquisitions=10 contended=4 wall_ms=20 wait_total_ms=8 wait_max_ms=3 timed_holds=5 hold_total_ms=2 hold_max_ms=1 off_cpu_total_ms=1.5 off_cpu_max_ms=0.9 off_cpu_holds_over_50us=2 top=[]
Sleep queue stats: acquisitions=20 contended=2 wall_ms=40 wait_total_ms=2 wait_max_ms=1 timed_holds=3 hold_total_ms=1 hold_max_ms=0.5 off_cpu_total_ms=0.5 off_cpu_max_ms=0.4 off_cpu_holds_over_50us=1 top=[0:0acq/0cont/0ch/0x0wc/0.000wait_ms]
"""
        intervals = summarize_sleepq.parse_intervals(text)
        result = summarize_sleepq.summarize(intervals, 1)
        self.assertEqual(result["acquisitions"], 20)
        self.assertEqual(result["intervals_selected"], 1)
        self.assertEqual(result["reported_hot_buckets"], [])


if __name__ == "__main__":
    unittest.main()
