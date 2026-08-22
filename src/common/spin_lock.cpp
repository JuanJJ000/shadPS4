// SPDX-FileCopyrightText: Copyright 2020 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/spin_lock.h"

#if _MSC_VER
#include <intrin.h>
#if _M_AMD64
#define __x86_64__ 1
#endif
#if _M_ARM64
#define __aarch64__ 1
#endif
#else
#if __x86_64__
#include <xmmintrin.h>
#endif
#endif

namespace {

void ThreadPause() {
#if __x86_64__
    _mm_pause();
#elif __aarch64__ && _MSC_VER
    __yield();
#elif __aarch64__
    asm("yield");
#endif
}

} // Anonymous namespace

namespace Common {

namespace {

struct alignas(64) AtomicSpinLockStats {
    std::atomic<std::uint64_t> acquisitions{};
    std::atomic<std::uint64_t> contended_acquisitions{};
    std::atomic<std::uint64_t> spin_iterations{};
    std::atomic<std::uint64_t> maximum_spin_iterations{};
    std::atomic<std::uint64_t> try_attempts{};
    std::atomic<std::uint64_t> try_failures{};
};

std::atomic<bool> spin_lock_stats_enabled{};
std::array<AtomicSpinLockStats, static_cast<std::size_t>(SpinLockClass::Count)> spin_lock_stats{};

void UpdateMaximum(std::atomic<std::uint64_t>& maximum, std::uint64_t value) {
    auto current = maximum.load(std::memory_order_relaxed);
    while (current < value &&
           !maximum.compare_exchange_weak(current, value, std::memory_order_relaxed)) {
    }
}

AtomicSpinLockStats& StatsFor(SpinLockClass lock_class) {
    return spin_lock_stats[static_cast<std::size_t>(lock_class)];
}

} // namespace

void SetSpinLockStatsEnabled(bool enabled) {
    if (enabled && !spin_lock_stats_enabled.load(std::memory_order_relaxed)) {
        static_cast<void>(ConsumeSpinLockStats());
    }
    spin_lock_stats_enabled.store(enabled, std::memory_order_relaxed);
}

bool SpinLockStatsEnabled() {
    return spin_lock_stats_enabled.load(std::memory_order_relaxed);
}

SpinLockStatsArray ConsumeSpinLockStats() {
    SpinLockStatsArray snapshot{};
    for (std::size_t index = 0; index < spin_lock_stats.size(); ++index) {
        auto& source = spin_lock_stats[index];
        auto& target = snapshot[index];
        target.acquisitions = source.acquisitions.exchange(0, std::memory_order_relaxed);
        target.contended_acquisitions =
            source.contended_acquisitions.exchange(0, std::memory_order_relaxed);
        target.spin_iterations = source.spin_iterations.exchange(0, std::memory_order_relaxed);
        target.maximum_spin_iterations =
            source.maximum_spin_iterations.exchange(0, std::memory_order_relaxed);
        target.try_attempts = source.try_attempts.exchange(0, std::memory_order_relaxed);
        target.try_failures = source.try_failures.exchange(0, std::memory_order_relaxed);
    }
    return snapshot;
}

void SpinLock::lock() {
    if (!SpinLockStatsEnabled()) {
        while (lck.test_and_set(std::memory_order_acquire)) {
            ThreadPause();
        }
        return;
    }

    std::uint64_t spins = 0;
    while (lck.test_and_set(std::memory_order_acquire)) {
        ++spins;
        ThreadPause();
    }
    auto& stats = StatsFor(lock_class);
    stats.acquisitions.fetch_add(1, std::memory_order_relaxed);
    stats.spin_iterations.fetch_add(spins, std::memory_order_relaxed);
    if (spins != 0) {
        stats.contended_acquisitions.fetch_add(1, std::memory_order_relaxed);
        UpdateMaximum(stats.maximum_spin_iterations, spins);
    }
}

void SpinLock::unlock() {
    lck.clear(std::memory_order_release);
}

bool SpinLock::try_lock() {
    const bool acquired = !lck.test_and_set(std::memory_order_acquire);
    if (SpinLockStatsEnabled()) {
        auto& stats = StatsFor(lock_class);
        stats.try_attempts.fetch_add(1, std::memory_order_relaxed);
        if (acquired) {
            stats.acquisitions.fetch_add(1, std::memory_order_relaxed);
        } else {
            stats.try_failures.fetch_add(1, std::memory_order_relaxed);
        }
    }
    if (!acquired) {
        return false;
    }
    return true;
}

} // namespace Common
