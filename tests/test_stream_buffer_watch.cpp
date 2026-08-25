// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstddef>

#include <gtest/gtest.h>

#include "video_core/buffer_cache/stream_buffer_watch.h"

using VideoCore::Detail::RecordStreamBufferWatch;
using VideoCore::Detail::PendingStreamBufferWatch;
using VideoCore::Detail::StreamBufferWatch;
using VideoCore::Detail::StreamBufferWatchResult;

TEST(StreamBufferWatch, CoalescesOneWatchPerTick) {
    std::array<StreamBufferWatch, 3> watches{};
    std::size_t cursor{};

    EXPECT_EQ(RecordStreamBufferWatch(watches, cursor, 7, 64),
              StreamBufferWatchResult::Appended);
    EXPECT_EQ(cursor, 1);
    EXPECT_EQ(watches[0].tick, 7);
    EXPECT_EQ(watches[0].upper_bound, 64);

    EXPECT_EQ(RecordStreamBufferWatch(watches, cursor, 7, 192),
              StreamBufferWatchResult::Coalesced);
    EXPECT_EQ(cursor, 1);
    EXPECT_EQ(watches[0].upper_bound, 192);

    EXPECT_EQ(RecordStreamBufferWatch(watches, cursor, 8, 256),
              StreamBufferWatchResult::Appended);
    EXPECT_EQ(cursor, 2);
    EXPECT_EQ(watches[1].tick, 8);
    EXPECT_EQ(watches[1].upper_bound, 256);
}

TEST(StreamBufferWatch, ReportsFullWithoutChangingState) {
    std::array<StreamBufferWatch, 1> watches{{{.tick = 3, .upper_bound = 128}}};
    std::size_t cursor{1};

    EXPECT_EQ(RecordStreamBufferWatch(watches, cursor, 4, 256),
              StreamBufferWatchResult::Full);
    EXPECT_EQ(cursor, 1);
    EXPECT_EQ(watches[0].tick, 3);
    EXPECT_EQ(watches[0].upper_bound, 128);
}

TEST(StreamBufferWatch, DefersLatestBoundUntilSubmission) {
    PendingStreamBufferWatch pending;
    std::array<StreamBufferWatch, 3> watches{};
    std::size_t cursor{};

    pending.Commit(64);
    pending.Commit(192);
    EXPECT_TRUE(pending.HasPending());
    EXPECT_EQ(cursor, 0);

    EXPECT_EQ(pending.Record(watches, cursor, 7), StreamBufferWatchResult::Appended);
    EXPECT_FALSE(pending.HasPending());
    EXPECT_EQ(cursor, 1);
    EXPECT_EQ(watches[0].tick, 7);
    EXPECT_EQ(watches[0].upper_bound, 192);

    pending.Commit(256);
    EXPECT_EQ(pending.Record(watches, cursor, 8), StreamBufferWatchResult::Appended);
    EXPECT_EQ(cursor, 2);
    EXPECT_EQ(watches[1].tick, 8);
    EXPECT_EQ(watches[1].upper_bound, 256);
}

TEST(StreamBufferWatch, RetainsPendingBoundWhenStorageIsFull) {
    PendingStreamBufferWatch pending;
    std::array<StreamBufferWatch, 1> full_watches{{{.tick = 3, .upper_bound = 128}}};
    std::size_t cursor{1};

    pending.Commit(256);
    EXPECT_EQ(pending.Record(full_watches, cursor, 4), StreamBufferWatchResult::Full);
    EXPECT_TRUE(pending.HasPending());

    std::array<StreamBufferWatch, 2> grown_watches{{full_watches[0], {}}};
    EXPECT_EQ(pending.Record(grown_watches, cursor, 4), StreamBufferWatchResult::Appended);
    EXPECT_FALSE(pending.HasPending());
    EXPECT_EQ(cursor, 2);
    EXPECT_EQ(grown_watches[1].tick, 4);
    EXPECT_EQ(grown_watches[1].upper_bound, 256);
}
