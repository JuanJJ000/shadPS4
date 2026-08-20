// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>

#include "common/logging/log.h"

#include <vk_mem_alloc.h>

namespace Vulkan {

inline void LogVmaHeapBudgets(VmaAllocator allocator) {
    std::array<VmaBudget, VK_MAX_MEMORY_HEAPS> budgets{};
    vmaGetHeapBudgets(allocator, budgets.data());

    bool found_heap = false;
    for (size_t heap = 0; heap < budgets.size(); ++heap) {
        const VmaBudget& budget = budgets[heap];
        const VmaStatistics& stats = budget.statistics;
        if (budget.usage == 0 && budget.budget == 0 && stats.blockBytes == 0 &&
            stats.allocationBytes == 0) {
            continue;
        }
        found_heap = true;
        LOG_CRITICAL(Render_Vulkan,
                     "VMA heap {}: usage {} / budget {} bytes, block bytes {}, allocation bytes "
                     "{}, blocks {}, allocations {}",
                     heap, budget.usage, budget.budget, stats.blockBytes, stats.allocationBytes,
                     stats.blockCount, stats.allocationCount);
    }
    if (!found_heap) {
        LOG_CRITICAL(Render_Vulkan, "VMA returned no populated heap budgets");
    }
}

} // namespace Vulkan
