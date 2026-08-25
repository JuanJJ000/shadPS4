# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import unittest
from pathlib import Path


RASTERIZER = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "video_core"
    / "renderer_vulkan"
    / "vk_rasterizer.cpp"
)


class SharedMemoryStreamCommitTests(unittest.TestCase):
    def test_zeroed_reservation_is_committed_before_binding(self) -> None:
        source = RASTERIZER.read_text(encoding="utf-8")
        branch_start = source.index(
            "desc.buffer_type == Shader::BufferType::SharedMemory"
        )
        branch_end = source.index("} else {", branch_start)
        branch = source[branch_start:branch_end]

        operations = [
            branch.index("lds_buffer.Map("),
            branch.index("std::memset("),
            branch.index("lds_buffer.Commit();"),
            branch.index("buffer_infos.emplace_back("),
        ]
        self.assertEqual(operations, sorted(operations))


if __name__ == "__main__":
    unittest.main()
