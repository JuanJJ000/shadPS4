// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <shared_mutex>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

#include "common/atomic_shared_first_mutex.h"

using Common::AtomicSharedFirstMutex;

TEST(AtomicSharedFirstMutex, AllowsConcurrentAndRecursiveReaders) {
    AtomicSharedFirstMutex mutex;
    mutex.lock_shared();
    EXPECT_TRUE(mutex.try_lock_shared());
    EXPECT_FALSE(mutex.try_lock());
    mutex.unlock_shared();
    mutex.unlock_shared();
    EXPECT_TRUE(mutex.try_lock());
    mutex.unlock();
}

TEST(AtomicSharedFirstMutex, WriterExcludesReadersAndWriters) {
    AtomicSharedFirstMutex mutex;
    mutex.lock();
    EXPECT_FALSE(mutex.try_lock());
    EXPECT_FALSE(mutex.try_lock_shared());
    mutex.unlock();
    EXPECT_TRUE(mutex.try_lock_shared());
    mutex.unlock_shared();
}

TEST(AtomicSharedFirstMutex, AdmitsReaderAheadOfWaitingWriter) {
    AtomicSharedFirstMutex mutex;
    mutex.lock_shared();
    std::atomic<bool> writer_waiting{};
    std::atomic<bool> writer_entered{};
    std::jthread writer{[&] {
        writer_waiting.store(true, std::memory_order_release);
        std::unique_lock lock{mutex};
        writer_entered.store(true, std::memory_order_release);
    }};
    while (!writer_waiting.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }

    EXPECT_TRUE(mutex.try_lock_shared());
    EXPECT_FALSE(writer_entered.load(std::memory_order_acquire));
    mutex.unlock_shared();
    mutex.unlock_shared();
}

TEST(AtomicSharedFirstMutex, PreservesMutualExclusionUnderContention) {
    AtomicSharedFirstMutex mutex;
    std::atomic<bool> stop{};
    std::atomic<bool> failed{};
    std::atomic<bool> writer_started{};
    std::uint64_t value{};
    std::uint64_t inverse{~value};
    std::vector<std::jthread> workers;
    workers.emplace_back([&] {
        while (!stop.load(std::memory_order_relaxed)) {
            std::unique_lock lock{mutex};
            ++value;
            std::this_thread::yield();
            inverse = ~value;
            writer_started.store(true, std::memory_order_release);
        }
    });
    while (!writer_started.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }
    for (int i = 0; i < 4; ++i) {
        workers.emplace_back([&] {
            while (!stop.load(std::memory_order_relaxed)) {
                std::shared_lock lock{mutex};
                if (inverse != ~value) {
                    failed.store(true, std::memory_order_relaxed);
                }
            }
        });
    }

    std::this_thread::sleep_for(std::chrono::milliseconds{250});
    stop.store(true, std::memory_order_relaxed);
    workers.clear();
    EXPECT_FALSE(failed.load(std::memory_order_relaxed));
    EXPECT_GT(value, 0);
}

TEST(AtomicSharedFirstMutex, SupportsMixedScopedLock) {
    AtomicSharedFirstMutex mutex;
    std::mutex companion;
    {
        std::scoped_lock lock{mutex, companion};
        EXPECT_FALSE(mutex.try_lock_shared());
    }
    EXPECT_TRUE(mutex.try_lock_shared());
    mutex.unlock_shared();
}
