// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <atomic>
#include <cstdint>

namespace Common {

// Like std::shared_mutex, but readers have priority over waiting writers.
// The uncontended reader path does not enter a kernel-backed mutex.
class AtomicSharedFirstMutex {
public:
    void lock() {
        std::uint32_t expected = Unlocked;
        while (!state.compare_exchange_weak(expected, Writer, std::memory_order_acquire,
                                            std::memory_order_relaxed)) {
            state.wait(expected, std::memory_order_relaxed);
            expected = Unlocked;
        }
    }

    bool try_lock() {
        std::uint32_t expected = Unlocked;
        return state.compare_exchange_strong(expected, Writer, std::memory_order_acquire,
                                             std::memory_order_relaxed);
    }

    void unlock() {
        state.store(Unlocked, std::memory_order_release);
        state.notify_all();
    }

    void lock_shared() {
        std::uint32_t current = state.load(std::memory_order_relaxed);
        for (;;) {
            while (current == Writer) {
                state.wait(Writer, std::memory_order_relaxed);
                current = state.load(std::memory_order_relaxed);
            }
            if (current == MaxReaders) {
                state.wait(MaxReaders, std::memory_order_relaxed);
                current = state.load(std::memory_order_relaxed);
                continue;
            }
            if (state.compare_exchange_weak(current, current + 1, std::memory_order_acquire,
                                            std::memory_order_relaxed)) {
                return;
            }
        }
    }

    bool try_lock_shared() {
        std::uint32_t current = state.load(std::memory_order_relaxed);
        while (current != Writer && current != MaxReaders) {
            if (state.compare_exchange_weak(current, current + 1, std::memory_order_acquire,
                                            std::memory_order_relaxed)) {
                return true;
            }
        }
        return false;
    }

    void unlock_shared() {
        const std::uint32_t previous = state.fetch_sub(1, std::memory_order_release);
        if (previous == 1) {
            state.notify_all();
        }
    }

private:
    static constexpr std::uint32_t Writer = UINT32_MAX;
    static constexpr std::uint32_t MaxReaders = Writer - 1;
    static constexpr std::uint32_t Unlocked = 0;
    std::atomic<std::uint32_t> state{Unlocked};
};

} // namespace Common
