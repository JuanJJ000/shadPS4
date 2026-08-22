// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <string_view>
#if defined(__linux__)
#include <time.h>
#endif
#include "common/logging/log.h"
#include "common/spin_lock.h"
#include "core/libraries/kernel/threads/pthread.h"
#include "core/libraries/kernel/threads/sleepq.h"

namespace Libraries::Kernel {

static constexpr int HASHSHIFT = 9;
static constexpr int HASHSIZE = (1 << HASHSHIFT);
static constexpr u64 DefaultStatsInterval = 1'048'576;
static constexpr u64 UncontendedHoldSampleMask = 255;
static constexpr size_t StatsThreadClassCount = 5;
#define SC_HASH(wchan)                                                                             \
    ((u32)((((uintptr_t)(wchan) >> 3) ^ ((uintptr_t)(wchan) >> (HASHSHIFT + 3))) & (HASHSIZE - 1)))
#define SC_LOOKUP(wc) &sc_table[SC_HASH(wc)]

struct SleepQueueStatsConfig {
    bool enabled{};
    u64 interval{DefaultStatsInterval};
    u64 spin_yield_after{};
};

static const SleepQueueStatsConfig& GetStatsConfig() {
    static const SleepQueueStatsConfig config = [] {
        SleepQueueStatsConfig result{};
        if (const char* value = std::getenv("SHADPS4_SLEEPQ_STATS")) {
            result.enabled = value[0] != '\0' && value[0] != '0';
        }
        if (const char* value = std::getenv("SHADPS4_SLEEPQ_STATS_INTERVAL")) {
            char* end = nullptr;
            const auto parsed = std::strtoull(value, &end, 10);
            if (end != value && *end == '\0' && parsed >= 1'024 && parsed <= 1'000'000'000) {
                result.interval = parsed;
            }
        }
        if (const char* value = std::getenv("SHADPS4_SLEEPQ_SPIN_YIELD_AFTER")) {
            char* end = nullptr;
            const auto parsed = std::strtoull(value, &end, 10);
            if (end != value && *end == '\0' &&
                (parsed == 0 ||
                 (parsed >= 32 && parsed <= 65'536 && (parsed & (parsed - 1)) == 0))) {
                result.spin_yield_after = parsed;
            } else {
                LOG_WARNING(Kernel_Pthread,
                            "Ignoring invalid sleep-queue spin-yield threshold '{}'; expected 0 "
                            "or a power of two from 32 through 65536",
                            value);
            }
        }
        return result;
    }();
    return config;
}

static u64 SteadyClockNanoseconds() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

static u64 ThreadClockNanoseconds() {
#if defined(__linux__)
    timespec time{};
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &time) == 0) {
        return static_cast<u64>(time.tv_sec) * 1'000'000'000ULL + static_cast<u64>(time.tv_nsec);
    }
#endif
    return 0;
}

static void RecordMaximum(std::atomic<u64>& maximum, u64 value) {
    u64 previous = maximum.load(std::memory_order_relaxed);
    while (previous < value &&
           !maximum.compare_exchange_weak(previous, value, std::memory_order_relaxed)) {
    }
}

enum class StatsThreadClass : u8 {
    Unknown,
    GameMain,
    JobWorker,
    MoviePlayer,
    Other,
};

static StatsThreadClass GetStatsThreadClass() {
    if (g_curthread == nullptr) {
        return StatsThreadClass::Unknown;
    }
    const std::string_view name{g_curthread->name};
    if (name == "Game:Main") {
        return StatsThreadClass::GameMain;
    }
    if (name.starts_with("JobWorker")) {
        return StatsThreadClass::JobWorker;
    }
    if (name == "MoviePlayer") {
        return StatsThreadClass::MoviePlayer;
    }
    return StatsThreadClass::Other;
}

struct alignas(64) SleepQueueBucketStats {
    std::atomic<u64> acquisitions{};
    std::atomic<u64> contended{};
    std::atomic<u64> wchan_changes{};
    std::atomic<uintptr_t> latest_wchan{};
    std::atomic<u64> wait_nanoseconds{};
    std::atomic<u64> maximum_wait_nanoseconds{};
    std::atomic<u64> timed_holds{};
    std::atomic<u64> hold_nanoseconds{};
    std::atomic<u64> maximum_hold_nanoseconds{};
    std::atomic<u64> off_cpu_nanoseconds{};
    std::atomic<u64> maximum_off_cpu_nanoseconds{};
    std::atomic<u64> long_off_cpu_holds{};
    std::array<std::atomic<u64>, StatsThreadClassCount> contended_owners{};
    std::array<std::atomic<u64>, StatsThreadClassCount> contended_waiters{};
    std::array<std::atomic<u64>, StatsThreadClassCount> off_cpu_by_owner{};
};

static std::array<SleepQueueBucketStats, HASHSIZE> sc_stats{};
static std::atomic<u64> sc_completed_acquisitions{};
static std::atomic<u64> sc_stats_interval_started_nanoseconds{SteadyClockNanoseconds()};

struct SleepQueueChain {
    Common::TaggedSpinLock<Common::SpinLockClass::SleepQueue> sc_lock;
    SleepqList sc_queues;
    int sc_type;
    u64 acquired_wall_nanoseconds{};
    u64 acquired_thread_nanoseconds{};
    uintptr_t latest_stats_wchan{};
    std::atomic<u8> owner_class{};
    bool timed_hold{};
};

static std::array<SleepQueueChain, HASHSIZE> sc_table{};

struct SleepQueueStatsSnapshot {
    u64 bucket{};
    u64 acquisitions{};
    u64 contended{};
    u64 wchan_changes{};
    uintptr_t latest_wchan{};
    u64 wait_nanoseconds{};
    u64 maximum_wait_nanoseconds{};
    u64 timed_holds{};
    u64 hold_nanoseconds{};
    u64 maximum_hold_nanoseconds{};
    u64 off_cpu_nanoseconds{};
    u64 maximum_off_cpu_nanoseconds{};
    u64 long_off_cpu_holds{};
    std::array<u64, StatsThreadClassCount> contended_owners{};
    std::array<u64, StatsThreadClassCount> contended_waiters{};
    std::array<u64, StatsThreadClassCount> off_cpu_by_owner{};
};

static void LogSleepQueueStats() {
    const u64 interval_finished_nanoseconds = SteadyClockNanoseconds();
    const u64 interval_started_nanoseconds =
        sc_stats_interval_started_nanoseconds.exchange(interval_finished_nanoseconds,
                                                       std::memory_order_relaxed);
    const u64 interval_wall_nanoseconds =
        interval_finished_nanoseconds - interval_started_nanoseconds;
    SleepQueueStatsSnapshot total{};
    std::array<SleepQueueStatsSnapshot, 3> hottest{};
    for (u64 bucket = 0; bucket < HASHSIZE; ++bucket) {
        auto& source = sc_stats[bucket];
        SleepQueueStatsSnapshot current{
            .bucket = bucket,
            .acquisitions = source.acquisitions.exchange(0, std::memory_order_relaxed),
            .contended = source.contended.exchange(0, std::memory_order_relaxed),
            .wchan_changes = source.wchan_changes.exchange(0, std::memory_order_relaxed),
            .latest_wchan = source.latest_wchan.load(std::memory_order_relaxed),
            .wait_nanoseconds = source.wait_nanoseconds.exchange(0, std::memory_order_relaxed),
            .maximum_wait_nanoseconds =
                source.maximum_wait_nanoseconds.exchange(0, std::memory_order_relaxed),
            .timed_holds = source.timed_holds.exchange(0, std::memory_order_relaxed),
            .hold_nanoseconds = source.hold_nanoseconds.exchange(0, std::memory_order_relaxed),
            .maximum_hold_nanoseconds =
                source.maximum_hold_nanoseconds.exchange(0, std::memory_order_relaxed),
            .off_cpu_nanoseconds =
                source.off_cpu_nanoseconds.exchange(0, std::memory_order_relaxed),
            .maximum_off_cpu_nanoseconds =
                source.maximum_off_cpu_nanoseconds.exchange(0, std::memory_order_relaxed),
            .long_off_cpu_holds =
                source.long_off_cpu_holds.exchange(0, std::memory_order_relaxed),
        };
        for (size_t thread_class = 0; thread_class < StatsThreadClassCount; ++thread_class) {
            current.contended_owners[thread_class] =
                source.contended_owners[thread_class].exchange(0, std::memory_order_relaxed);
            current.contended_waiters[thread_class] =
                source.contended_waiters[thread_class].exchange(0, std::memory_order_relaxed);
            current.off_cpu_by_owner[thread_class] =
                source.off_cpu_by_owner[thread_class].exchange(0, std::memory_order_relaxed);
        }
        total.acquisitions += current.acquisitions;
        total.contended += current.contended;
        total.wchan_changes += current.wchan_changes;
        total.wait_nanoseconds += current.wait_nanoseconds;
        total.maximum_wait_nanoseconds =
            std::max(total.maximum_wait_nanoseconds, current.maximum_wait_nanoseconds);
        total.timed_holds += current.timed_holds;
        total.hold_nanoseconds += current.hold_nanoseconds;
        total.maximum_hold_nanoseconds =
            std::max(total.maximum_hold_nanoseconds, current.maximum_hold_nanoseconds);
        total.off_cpu_nanoseconds += current.off_cpu_nanoseconds;
        total.maximum_off_cpu_nanoseconds =
            std::max(total.maximum_off_cpu_nanoseconds, current.maximum_off_cpu_nanoseconds);
        total.long_off_cpu_holds += current.long_off_cpu_holds;
        for (size_t thread_class = 0; thread_class < StatsThreadClassCount; ++thread_class) {
            total.contended_owners[thread_class] += current.contended_owners[thread_class];
            total.contended_waiters[thread_class] += current.contended_waiters[thread_class];
            total.off_cpu_by_owner[thread_class] += current.off_cpu_by_owner[thread_class];
        }
        if (current.wait_nanoseconds > hottest[0].wait_nanoseconds) {
            hottest[2] = hottest[1];
            hottest[1] = hottest[0];
            hottest[0] = current;
        } else if (current.wait_nanoseconds > hottest[1].wait_nanoseconds) {
            hottest[2] = hottest[1];
            hottest[1] = current;
        } else if (current.wait_nanoseconds > hottest[2].wait_nanoseconds) {
            hottest[2] = current;
        }
    }

    const double contention_percent =
        total.acquisitions != 0
            ? static_cast<double>(total.contended) * 100.0 / total.acquisitions
            : 0.0;
    const double wait_milliseconds = static_cast<double>(total.wait_nanoseconds) / 1'000'000.0;
    const double hold_milliseconds = static_cast<double>(total.hold_nanoseconds) / 1'000'000.0;
    const double off_cpu_milliseconds =
        static_cast<double>(total.off_cpu_nanoseconds) / 1'000'000.0;
    const double wall_milliseconds =
        static_cast<double>(interval_wall_nanoseconds) / 1'000'000.0;
    const double acquisition_rate =
        interval_wall_nanoseconds != 0
            ? static_cast<double>(total.acquisitions) * 1'000'000'000.0 /
                  interval_wall_nanoseconds
            : 0.0;
    const double wait_share = interval_wall_nanoseconds != 0
                                  ? static_cast<double>(total.wait_nanoseconds) * 100.0 /
                                        interval_wall_nanoseconds
                                  : 0.0;
    const double sampled_off_cpu_share =
        total.hold_nanoseconds != 0
            ? static_cast<double>(total.off_cpu_nanoseconds) * 100.0 / total.hold_nanoseconds
            : 0.0;
    LOG_INFO(
        Kernel_Pthread,
        "Sleep queue stats: acquisitions={} contended={} contention_pct={:.3f} "
        "wchan_changes={} wall_ms={:.3f} acquisition_rate={:.1f} "
        "wait_total_ms={:.3f} wait_max_ms={:.3f} timed_holds={} hold_total_ms={:.3f} "
        "hold_max_ms={:.3f} off_cpu_total_ms={:.3f} off_cpu_max_ms={:.3f} "
        "off_cpu_holds_over_50us={} wait_share_pct={:.1f} sampled_off_cpu_share_pct={:.1f} "
        "contention_owners=[unknown:{},main:{},workers:{},movie:{},other:{}] "
        "contention_waiters=[unknown:{},main:{},workers:{},movie:{},other:{}] "
        "off_cpu_owner_ms=[unknown:{:.3f},main:{:.3f},workers:{:.3f},movie:{:.3f},"
        "other:{:.3f}] "
        "top=[{}:{}acq/{}cont/{}ch/{:#x}wc/{:.3f}wait_ms, "
        "{}:{}acq/{}cont/{}ch/{:#x}wc/{:.3f}wait_ms, "
        "{}:{}acq/{}cont/{}ch/{:#x}wc/{:.3f}wait_ms]",
        total.acquisitions, total.contended, contention_percent, total.wchan_changes,
        wall_milliseconds, acquisition_rate, wait_milliseconds,
        static_cast<double>(total.maximum_wait_nanoseconds) / 1'000'000.0, total.timed_holds,
        hold_milliseconds, static_cast<double>(total.maximum_hold_nanoseconds) / 1'000'000.0,
        off_cpu_milliseconds,
        static_cast<double>(total.maximum_off_cpu_nanoseconds) / 1'000'000.0,
        total.long_off_cpu_holds, wait_share, sampled_off_cpu_share, total.contended_owners[0],
        total.contended_owners[1], total.contended_owners[2], total.contended_owners[3],
        total.contended_owners[4], total.contended_waiters[0], total.contended_waiters[1],
        total.contended_waiters[2], total.contended_waiters[3], total.contended_waiters[4],
        static_cast<double>(total.off_cpu_by_owner[0]) / 1'000'000.0,
        static_cast<double>(total.off_cpu_by_owner[1]) / 1'000'000.0,
        static_cast<double>(total.off_cpu_by_owner[2]) / 1'000'000.0,
        static_cast<double>(total.off_cpu_by_owner[3]) / 1'000'000.0,
        static_cast<double>(total.off_cpu_by_owner[4]) / 1'000'000.0, hottest[0].bucket,
        hottest[0].acquisitions, hottest[0].contended, hottest[0].wchan_changes,
        hottest[0].latest_wchan,
        static_cast<double>(hottest[0].wait_nanoseconds) / 1'000'000.0, hottest[1].bucket,
        hottest[1].acquisitions, hottest[1].contended, hottest[1].wchan_changes,
        hottest[1].latest_wchan,
        static_cast<double>(hottest[1].wait_nanoseconds) / 1'000'000.0, hottest[2].bucket,
        hottest[2].acquisitions, hottest[2].contended, hottest[2].wchan_changes,
        hottest[2].latest_wchan,
        static_cast<double>(hottest[2].wait_nanoseconds) / 1'000'000.0);
}

void SleepqLock(void* wchan) {
    if (g_curthread != nullptr) {
        g_curthread->locklevel.fetch_add(1, std::memory_order_acq_rel);
    }
    const u32 bucket = SC_HASH(wchan);
    SleepQueueChain* sc = &sc_table[bucket];
    if (!GetStatsConfig().enabled) {
        sc->sc_lock.lock_with_yield_after(GetStatsConfig().spin_yield_after);
        return;
    }

    auto& stats = sc_stats[bucket];
    const auto current_thread_class = GetStatsThreadClass();
    bool contended = false;
    u64 wait_started_nanoseconds = 0;
    if (!sc->sc_lock.try_lock()) {
        contended = true;
        const size_t owner_class = sc->owner_class.load(std::memory_order_acquire);
        stats.contended_owners[owner_class].fetch_add(1, std::memory_order_relaxed);
        stats.contended_waiters[static_cast<size_t>(current_thread_class)].fetch_add(
            1, std::memory_order_relaxed);
        wait_started_nanoseconds = SteadyClockNanoseconds();
        sc->sc_lock.lock_with_yield_after(GetStatsConfig().spin_yield_after);
    }
    sc->owner_class.store(static_cast<u8>(current_thread_class), std::memory_order_release);
    const u64 acquisition = stats.acquisitions.fetch_add(1, std::memory_order_relaxed) + 1;
    const uintptr_t current_wchan = reinterpret_cast<uintptr_t>(wchan);
    if (sc->latest_stats_wchan != current_wchan) {
        sc->latest_stats_wchan = current_wchan;
        stats.wchan_changes.fetch_add(1, std::memory_order_relaxed);
        stats.latest_wchan.store(current_wchan, std::memory_order_relaxed);
    }
    if (contended) {
        const u64 wait_nanoseconds = SteadyClockNanoseconds() - wait_started_nanoseconds;
        stats.contended.fetch_add(1, std::memory_order_relaxed);
        stats.wait_nanoseconds.fetch_add(wait_nanoseconds, std::memory_order_relaxed);
        RecordMaximum(stats.maximum_wait_nanoseconds, wait_nanoseconds);
    }
    sc->timed_hold = contended || (acquisition & UncontendedHoldSampleMask) == 0;
    if (sc->timed_hold) {
        sc->acquired_wall_nanoseconds = SteadyClockNanoseconds();
        sc->acquired_thread_nanoseconds = ThreadClockNanoseconds();
    }
}

void SleepqUnlock(void* wchan) {
    const u32 bucket = SC_HASH(wchan);
    SleepQueueChain* sc = &sc_table[bucket];
    bool report_stats = false;
    if (GetStatsConfig().enabled && sc->timed_hold) {
        const u64 released_wall_nanoseconds = SteadyClockNanoseconds();
        const u64 released_thread_nanoseconds = ThreadClockNanoseconds();
        const u64 hold_nanoseconds = released_wall_nanoseconds - sc->acquired_wall_nanoseconds;
        const u64 thread_nanoseconds =
            released_thread_nanoseconds >= sc->acquired_thread_nanoseconds
                ? released_thread_nanoseconds - sc->acquired_thread_nanoseconds
                : 0;
        const u64 off_cpu_nanoseconds =
            sc->acquired_thread_nanoseconds != 0 && hold_nanoseconds > thread_nanoseconds
                ? hold_nanoseconds - thread_nanoseconds
                : 0;
        auto& stats = sc_stats[bucket];
        stats.timed_holds.fetch_add(1, std::memory_order_relaxed);
        stats.hold_nanoseconds.fetch_add(hold_nanoseconds, std::memory_order_relaxed);
        stats.off_cpu_nanoseconds.fetch_add(off_cpu_nanoseconds, std::memory_order_relaxed);
        stats.off_cpu_by_owner[sc->owner_class.load(std::memory_order_relaxed)].fetch_add(
            off_cpu_nanoseconds, std::memory_order_relaxed);
        stats.long_off_cpu_holds.fetch_add(off_cpu_nanoseconds >= 50'000,
                                           std::memory_order_relaxed);
        RecordMaximum(stats.maximum_hold_nanoseconds, hold_nanoseconds);
        RecordMaximum(stats.maximum_off_cpu_nanoseconds, off_cpu_nanoseconds);
    }
    sc->sc_lock.unlock();
    if (GetStatsConfig().enabled) {
        const u64 completed =
            sc_completed_acquisitions.fetch_add(1, std::memory_order_relaxed) + 1;
        report_stats = completed % GetStatsConfig().interval == 0;
    }
    if (g_curthread != nullptr) {
        const int previous = g_curthread->locklevel.fetch_sub(1, std::memory_order_acq_rel);
        ASSERT(previous > 0);
        if (previous == 1) {
            PthreadCancelInterrupt();
        }
    }
    if (report_stats) {
        LogSleepQueueStats();
    }
}

SleepQueue* SleepqLookup(void* wchan) {
    SleepQueueChain* sc = SC_LOOKUP(wchan);
    for (auto& sq : sc->sc_queues) {
        if (sq.sq_wchan == wchan) {
            return std::addressof(sq);
        }
    }
    return nullptr;
}

void SleepqAdd(void* wchan, Pthread* td) {
    SleepQueue* sq = SleepqLookup(wchan);
    if (sq != nullptr) {
        sq->sq_freeq.push_front(*td->sleepqueue);
    } else {
        SleepQueueChain* sc = SC_LOOKUP(wchan);
        sq = td->sleepqueue;
        sc->sc_queues.push_front(*sq);
        sq->sq_wchan = wchan;
        /* sq->sq_type = type; */
    }
    td->sleepqueue = nullptr;
    td->wchan = wchan;
    // libkernel uses a TAILQ here. Signal therefore selects the oldest waiter.
    sq->sq_blocked.push_back(td);
}

bool SleepqRemove(SleepQueue* sq, Pthread* td) {
    ASSERT_MSG(sq != nullptr, "Cannot remove a thread from a null sleep queue");
    if (sq == nullptr) [[unlikely]] {
        return false;
    }

    const auto removed = std::erase(sq->sq_blocked, td);
    const bool has_waiters = !sq->sq_blocked.empty();
    ASSERT_MSG(removed == 1, "Thread is missing from its sleep queue");
    if (removed == 0) [[unlikely]] {
        return has_waiters;
    }

    td->wchan = nullptr;
    if (!has_waiters) {
        td->sleepqueue = sq;
        sq->unlink();
        return false;
    }

    ASSERT_MSG(!sq->sq_freeq.empty(), "Sleep queue free list is empty while waiters remain");
    td->sleepqueue = std::addressof(sq->sq_freeq.front());
    sq->sq_freeq.pop_front();
    return true;
}

void SleepqDrop(SleepQueue* sq, void (*callback)(Pthread*, void*), void* arg) {
    if (sq->sq_blocked.empty()) {
        return;
    }

    sq->unlink();
    Pthread* td = sq->sq_blocked.front();
    sq->sq_blocked.pop_front();

    callback(td, arg);

    td->sleepqueue = sq;
    td->wchan = nullptr;

    auto sq2 = sq->sq_freeq.begin();
    for (Pthread* td2 : sq->sq_blocked) {
        callback(td2, arg);
        td2->sleepqueue = std::addressof(*sq2);
        td2->wchan = nullptr;
        ++sq2;
    }
    sq->sq_blocked.clear();
    sq->sq_freeq.clear();
}

} // namespace Libraries::Kernel
