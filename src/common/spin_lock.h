// SPDX-FileCopyrightText: Copyright 2020 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <cstdint>

namespace Common {

enum class SpinLockClass : std::uint8_t {
    Generic,
    PageManager,
    RegionManager,
    SlabHeap,
    SleepQueue,
    Count,
};

struct SpinLockStats {
    std::uint64_t acquisitions{};
    std::uint64_t contended_acquisitions{};
    std::uint64_t spin_iterations{};
    std::uint64_t maximum_spin_iterations{};
    std::uint64_t try_attempts{};
    std::uint64_t try_failures{};
    std::uint64_t yield_calls{};
};

using SpinLockStatsArray =
    std::array<SpinLockStats, static_cast<std::size_t>(SpinLockClass::Count)>;

void SetSpinLockStatsEnabled(bool enabled);
[[nodiscard]] bool SpinLockStatsEnabled();
[[nodiscard]] SpinLockStatsArray ConsumeSpinLockStats();

/**
 * SpinLock class
 * a lock similar to mutex that forces a thread to spin wait instead calling the
 * supervisor. Should be used on short sequences of code.
 */
class SpinLock {
public:
    explicit constexpr SpinLock(SpinLockClass lock_class_ = SpinLockClass::Generic)
        : lock_class{lock_class_} {}

    SpinLock(const SpinLock&) = delete;
    SpinLock& operator=(const SpinLock&) = delete;

    SpinLock(SpinLock&&) = delete;
    SpinLock& operator=(SpinLock&&) = delete;

    void lock();
    void lock_with_yield_after(std::uint64_t spin_iterations);
    void unlock();
    [[nodiscard]] bool try_lock();

private:
    std::atomic_flag lck = ATOMIC_FLAG_INIT;
    SpinLockClass lock_class;
};

template <SpinLockClass LockClass>
class TaggedSpinLock final : public SpinLock {
public:
    constexpr TaggedSpinLock() : SpinLock{LockClass} {}
};

} // namespace Common
