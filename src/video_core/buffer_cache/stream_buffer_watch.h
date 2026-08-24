// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstddef>
#include <span>

#include "common/types.h"

namespace VideoCore::Detail {

struct StreamBufferWatch {
    u64 tick{};
    u64 upper_bound{};
};

enum class StreamBufferWatchResult {
    Coalesced,
    Appended,
    Full,
};

inline StreamBufferWatchResult RecordStreamBufferWatch(std::span<StreamBufferWatch> watches,
                                                        std::size_t& cursor, u64 tick,
                                                        u64 upper_bound) {
    if (cursor > watches.size()) {
        return StreamBufferWatchResult::Full;
    }
    if (cursor != 0) {
        auto& last_watch = watches[cursor - 1];
        if (last_watch.tick == tick) {
            last_watch.upper_bound = upper_bound;
            return StreamBufferWatchResult::Coalesced;
        }
    }
    if (cursor == watches.size()) {
        return StreamBufferWatchResult::Full;
    }

    watches[cursor++] = StreamBufferWatch{
        .tick = tick,
        .upper_bound = upper_bound,
    };
    return StreamBufferWatchResult::Appended;
}

} // namespace VideoCore::Detail
