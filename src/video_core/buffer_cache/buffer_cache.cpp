// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <limits>
#include <string_view>
#include "common/alignment.h"
#include "common/debug.h"
#include "common/logging/log.h"
#include "common/scope_exit.h"
#include "core/memory.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/buffer_cache/memory_tracker.h"
#include "video_core/renderer_vulkan/vk_graphics_pipeline.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/texture_cache/texture_cache.h"

#include <vk_mem_alloc.h>

namespace VideoCore {

static constexpr size_t DataShareBufferSize = 64_KB;
static constexpr size_t StagingBufferSize = 512_MB;
static constexpr size_t DownloadBufferSize = 32_MB;
static constexpr size_t UboStreamBufferSize = 64_MB;
static constexpr size_t DeviceBufferSize = 128_MB;

static u64 SteadyClockNanoseconds() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

BufferCache::BufferCache(const Vulkan::Instance& instance_, Vulkan::Scheduler& scheduler_,
                         AmdGpu::Liverpool* liverpool_, TextureCache& texture_cache_,
                         PageManager& tracker)
    : instance{instance_}, scheduler{scheduler_}, liverpool{liverpool_},
      memory{Core::Memory::Instance()}, texture_cache{texture_cache_},
      fault_manager{instance, scheduler, *this, CACHING_PAGEBITS, CACHING_NUMPAGES},
      staging_buffer{instance, scheduler, MemoryUsage::Upload, StagingBufferSize},
      stream_buffer{instance, scheduler, MemoryUsage::Stream, UboStreamBufferSize},
      download_buffer{instance, scheduler, MemoryUsage::Download, DownloadBufferSize},
      device_buffer{instance, scheduler, MemoryUsage::DeviceLocal, DeviceBufferSize},
      gds_buffer{instance, scheduler, MemoryUsage::Stream, 0, AllFlags, DataShareBufferSize},
      bda_pagetable_buffer{instance, scheduler, MemoryUsage::DeviceLocal,
                           0,        AllFlags,  BDA_PAGETABLE_SIZE} {
    Vulkan::SetObjectName(instance.GetDevice(), gds_buffer.Handle(), "GDS Buffer");
    Vulkan::SetObjectName(instance.GetDevice(), bda_pagetable_buffer.Handle(),
                          "BDA Page Table Buffer");

    memory_tracker = std::make_unique<MemoryTracker>(tracker);

    std::memset(gds_buffer.mapped_data.data(), 0, DataShareBufferSize);

    const char* readback_stats = std::getenv("SHADPS4_PRECISE_READBACK_STATS");
    precise_readback_stats_enabled =
        readback_stats != nullptr && readback_stats[0] != '\0' && readback_stats[0] != '0';
    if (const char* phase_timing = std::getenv("SHADPS4_PRECISE_READBACK_PHASE_TIMING")) {
        const std::string_view value{phase_timing};
        if (value == "1") {
            precise_readback_phase_timing_enabled = precise_readback_stats_enabled;
            if (!precise_readback_stats_enabled) {
                LOG_WARNING(Render_Vulkan,
                            "Ignoring precise readback phase timing because readback statistics "
                            "are disabled");
            }
        } else if (value != "0" && !value.empty()) {
            LOG_WARNING(Render_Vulkan,
                        "Ignoring invalid precise readback phase timing '{}'; expected 0 or 1",
                        value);
        }
    }
    if (const char* interval = std::getenv("SHADPS4_PRECISE_READBACK_STATS_INTERVAL")) {
        char* end = nullptr;
        const auto parsed = std::strtoull(interval, &end, 10);
        if (end != interval && *end == '\0' && parsed >= 16 && parsed <= 65'536) {
            precise_readback_stats_interval = parsed;
        }
    }
    bool readback_window_overridden = false;
    if (const char* window_kib = std::getenv("SHADPS4_PRECISE_READBACK_WINDOW_KB")) {
        char* end = nullptr;
        const auto parsed = std::strtoull(window_kib, &end, 10);
        if (end != window_kib && *end == '\0' && parsed >= 4 && parsed <= 512 &&
            (parsed & (parsed - 1)) == 0) {
            precise_readback_window_size = parsed * 1_KB;
            readback_window_overridden = true;
        } else {
            LOG_WARNING(Render_Vulkan,
                        "Ignoring invalid precise readback window '{}'; expected a power-of-two "
                        "KiB value from 4 through 512",
                        window_kib);
        }
    }
    if (const char* site_window = std::getenv("SHADPS4_PRECISE_READBACK_WRITE_SITE_WINDOW")) {
        char* separator = nullptr;
        const auto parsed_pc = std::strtoull(site_window, &separator, 0);
        char* end = nullptr;
        const auto parsed_window =
            separator != nullptr && *separator == ':' ? std::strtoull(separator + 1, &end, 10) : 0;
        if (parsed_pc != 0 && separator != site_window && *separator == ':' &&
            end != separator + 1 && *end == '\0' && parsed_window >= 4 && parsed_window <= 512 &&
            (parsed_window & (parsed_window - 1)) == 0) {
            precise_readback_write_site_pc = parsed_pc;
            precise_readback_write_site_window_size = parsed_window * 1_KB;
        } else if (site_window[0] != '\0' && std::string_view{site_window} != "off") {
            LOG_WARNING(Render_Vulkan,
                        "Ignoring invalid precise write-site window '{}'; expected "
                        "<nonzero-pc>:<power-of-two-KiB-from-4-through-512>",
                        site_window);
        }
    }
    if (const char* probe_pc = std::getenv("SHADPS4_PRECISE_READBACK_WRITE_DISCARD_PROBE_PC")) {
        const std::string_view probe_value{probe_pc};
        char* end = nullptr;
        const auto parsed_pc = std::strtoull(probe_pc, &end, 0);
        if (probe_value.size() > 2 && probe_value[0] == '0' &&
            (probe_value[1] == 'x' || probe_value[1] == 'X') && parsed_pc != 0 && end != probe_pc &&
            *end == '\0') {
            precise_readback_write_discard_probe_pc = parsed_pc;
        } else if (!probe_value.empty() && probe_value != "off") {
            LOG_WARNING(Render_Vulkan,
                        "Ignoring invalid write-discard coverage probe PC '{}'; expected off or "
                        "a nonzero hexadecimal PC",
                        probe_pc);
        }
    }
    if (precise_readback_stats_enabled) {
        precise_readback_interval_started_nanoseconds = SteadyClockNanoseconds();
        LOG_INFO(Render_Vulkan,
                 "Precise readback counters enabled with a {}-request reporting interval and a "
                 "{} KiB window",
                 precise_readback_stats_interval, precise_readback_window_size / 1_KB);
    } else if (readback_window_overridden) {
        LOG_INFO(Render_Vulkan, "Precise readback window set to {} KiB",
                 precise_readback_window_size / 1_KB);
    }
    if (precise_readback_phase_timing_enabled) {
        LOG_INFO(Render_Vulkan,
                 "Precise readback phase timing enabled; prior GPU work is completed before the "
                 "current readback submission");
        const auto queue_families = instance.GetPhysicalDevice().getQueueFamilyProperties();
        const u32 queue_family = instance.GetGraphicsQueueFamilyIndex();
        if (queue_family < queue_families.size()) {
            precise_readback_gpu_timestamp_valid_bits =
                queue_families[queue_family].timestampValidBits;
            precise_readback_gpu_timestamp_period_ns =
                instance.GetPhysicalDevice().getProperties().limits.timestampPeriod;
        }
        if (precise_readback_gpu_timestamp_valid_bits != 0 &&
            precise_readback_gpu_timestamp_period_ns > 0.0) {
            const vk::QueryPoolCreateInfo query_pool_info = {
                .queryType = vk::QueryType::eTimestamp,
                .queryCount = 2,
            };
            auto [result, pool] = instance.GetDevice().createQueryPoolUnique(query_pool_info);
            if (result == vk::Result::eSuccess) {
                precise_readback_gpu_timestamp_pool = std::move(pool);
                LOG_INFO(Render_Vulkan,
                         "Precise readback GPU timestamps enabled with {} valid bits and a {:.3f} "
                         "ns period",
                         precise_readback_gpu_timestamp_valid_bits,
                         precise_readback_gpu_timestamp_period_ns);
            } else {
                LOG_WARNING(Render_Vulkan,
                            "Precise readback GPU timestamps unavailable: query pool creation "
                            "returned {}",
                            vk::to_string(result));
            }
        } else {
            LOG_WARNING(Render_Vulkan,
                        "Precise readback GPU timestamps unavailable on graphics queue {}",
                        queue_family);
        }
    }
    if (precise_readback_write_site_pc != 0) {
        LOG_INFO(Render_Vulkan, "Precise write-site window enabled for guest PC {:#x}: {} KiB",
                 precise_readback_write_site_pc, precise_readback_write_site_window_size / 1_KB);
    }
    if (precise_readback_write_discard_probe_pc != 0) {
        LOG_INFO(Render_Vulkan,
                 "Behavior-neutral write-discard coverage probe enabled for guest PC {:#x}",
                 precise_readback_write_discard_probe_pc);
    }

    // Set up garbage collection parameters
    if (!instance.CanReportMemoryUsage()) {
        trigger_gc_memory = DEFAULT_TRIGGER_GC_MEMORY;
        critical_gc_memory = DEFAULT_CRITICAL_GC_MEMORY;
        return;
    }

    const s64 device_local_memory = static_cast<s64>(instance.GetTotalMemoryBudget());
    const s64 min_spacing_expected = device_local_memory - 1_GB;
    const s64 min_spacing_critical = device_local_memory - 512_MB;
    const s64 mem_threshold = std::min<s64>(device_local_memory, TARGET_GC_THRESHOLD);
    const s64 min_vacancy_expected = (6 * mem_threshold) / 10;
    const s64 min_vacancy_critical = (2 * mem_threshold) / 10;
    trigger_gc_memory = static_cast<u64>(
        std::max<u64>(std::min(device_local_memory - min_vacancy_expected, min_spacing_expected),
                      DEFAULT_TRIGGER_GC_MEMORY));
    critical_gc_memory = static_cast<u64>(
        std::max<u64>(std::min(device_local_memory - min_vacancy_critical, min_spacing_critical),
                      DEFAULT_CRITICAL_GC_MEMORY));
}

BufferCache::~BufferCache() = default;

void BufferCache::InvalidateMemory(VAddr device_addr, u64 size,
                                   Common::FaultContext fault_context) {
    if (!IsRegionRegistered(device_addr, size)) {
        return;
    }
    memory_tracker->InvalidateRegion(device_addr, size, [this, device_addr, size, fault_context] {
        ReadMemory(device_addr, size, true, fault_context);
    });
}

void BufferCache::ReadMemory(VAddr device_addr, u64 size, bool is_write,
                             Common::FaultContext fault_context) {
    const u64 request_window_size = precise_readback_write_site_pc != 0 && is_write &&
                                            fault_context.rip == precise_readback_write_site_pc
                                        ? precise_readback_write_site_window_size
                                        : precise_readback_window_size;
    const u64 outstanding_depth =
        precise_readback_stats_enabled
            ? precise_readback_outstanding.fetch_add(1, std::memory_order_relaxed) + 1
            : 0;
    liverpool->SendCommand<true>([this, device_addr, size, is_write, fault_context,
                                  request_window_size, outstanding_depth] {
        SCOPE_EXIT {
            if (outstanding_depth != 0) {
                precise_readback_outstanding.fetch_sub(1, std::memory_order_relaxed);
            }
        };
        ReadbackDownloadSample request_sample{};
        const auto coverage = MeasureWriteDiscardCoverage(device_addr, is_write, fault_context);
        const VAddr device_addr_end = device_addr + size;
        ForEachBufferInRange(device_addr, size, [&](BufferId, Buffer& buffer) {
            // GPU-modified ranges come as many small scattered islands, so the download is
            // widened to a window around the intersection with each existing buffer.
            const VAddr buffer_start = buffer.CpuAddr();
            const VAddr buffer_end = buffer_start + buffer.SizeBytes();
            const VAddr intersection_start = std::max(device_addr, buffer_start);
            const VAddr intersection_end = std::min(device_addr_end, buffer_end);
            const VAddr window_start = std::max<VAddr>(
                Common::AlignDown(intersection_start, request_window_size), buffer_start);
            const VAddr window_end = std::min<VAddr>(
                std::max<VAddr>(window_start + request_window_size, intersection_end), buffer_end);
            const auto sample = DownloadBufferMemory(
                buffer, window_start, window_end - window_start, precise_readback_stats_enabled);
            if (precise_readback_stats_enabled) {
                RecordPreciseReadbackBufferStats(buffer, is_write, sample);
            }
            request_sample.bytes += sample.bytes;
            request_sample.call_count += sample.call_count;
            request_sample.copy_count += sample.copy_count;
            request_sample.finish_nanoseconds += sample.finish_nanoseconds;
            request_sample.gpu_copy_nanoseconds += sample.gpu_copy_nanoseconds;
            request_sample.gpu_timestamp_samples += sample.gpu_timestamp_samples;
            request_sample.gpu_timestamp_failures += sample.gpu_timestamp_failures;
            request_sample.prior_wait_nanoseconds += sample.prior_wait_nanoseconds;
            request_sample.submit_nanoseconds += sample.submit_nanoseconds;
            request_sample.current_wait_nanoseconds += sample.current_wait_nanoseconds;
            request_sample.wait_nanoseconds += sample.wait_nanoseconds;
        });
        if (is_write) {
            memory_tracker->MarkRegionAsCpuModified(device_addr, size);
        }
        if (precise_readback_stats_enabled) {
            RecordPreciseReadbackStats(device_addr, size, is_write, fault_context,
                                       request_window_size, outstanding_depth, request_sample,
                                       coverage);
        }
    });
}

BufferCache::WriteDiscardCoverageSample BufferCache::MeasureWriteDiscardCoverage(
    VAddr device_addr, bool is_write, const Common::FaultContext& fault_context) const {
    WriteDiscardCoverageSample sample{};
    if (precise_readback_write_discard_probe_pc == 0 || !is_write ||
        fault_context.rip != precise_readback_write_discard_probe_pc) {
        return sample;
    }
    sample.selector_hits = 1;

    // The selected Second Son site is a forward `rep movsq`. Reject a set direction flag,
    // implausibly large count, overflow, or a destination span that does not contain the fault.
    constexpr u64 DirectionFlag = 1ULL << 10;
    constexpr u64 MaximumProbeBytes = 1_MB;
    if ((fault_context.rflags & DirectionFlag) != 0 || fault_context.rcx == 0 ||
        fault_context.rcx > MaximumProbeBytes / sizeof(u64)) {
        return sample;
    }
    const u64 write_span_bytes = fault_context.rcx * sizeof(u64);
    if (fault_context.rdi > std::numeric_limits<VAddr>::max() - write_span_bytes) {
        return sample;
    }
    const VAddr write_end = fault_context.rdi + write_span_bytes;
    if (device_addr < fault_context.rdi || device_addr >= write_end) {
        return sample;
    }

    sample.valid_contexts = 1;
    sample.write_span_bytes = write_span_bytes;
    const VAddr page_start = Common::AlignDown(device_addr, ReadbackStatsPageSize);
    const VAddr page_end = page_start + ReadbackStatsPageSize;
    const VAddr page_write_start = std::max(page_start, fault_context.rdi);
    const VAddr page_write_end = std::min(page_end, write_end);
    sample.fault_page_write_bytes = page_write_end - page_write_start;
    gpu_modified_ranges.ForEachInRange(
        page_start, ReadbackStatsPageSize, [&](VAddr dirty_start, VAddr dirty_end) {
            sample.gpu_dirty_bytes += dirty_end - dirty_start;
            const VAddr covered_start = std::max(dirty_start, page_write_start);
            const VAddr covered_end = std::min(dirty_end, page_write_end);
            if (covered_start < covered_end) {
                sample.covered_dirty_bytes += covered_end - covered_start;
            }
        });
    sample.zero_dirty_requests = sample.gpu_dirty_bytes == 0;
    sample.fully_covered_requests =
        sample.gpu_dirty_bytes != 0 && sample.covered_dirty_bytes == sample.gpu_dirty_bytes;
    return sample;
}

void BufferCache::RecordPreciseReadbackStats(VAddr device_addr, u64 size, bool is_write,
                                             const Common::FaultContext& fault_context,
                                             u64 request_window_size, u64 outstanding_depth,
                                             const ReadbackDownloadSample& sample,
                                             const WriteDiscardCoverageSample& coverage) {
    precise_readback_sequence++;
    precise_readback_requests++;
    precise_readback_queued_requests += outstanding_depth > 1;
    precise_readback_outstanding_depth_sum += outstanding_depth;
    precise_readback_max_outstanding_depth =
        std::max(precise_readback_max_outstanding_depth, outstanding_depth);
    precise_readback_writes += is_write;
    precise_readback_requested_bytes += size;
    precise_readback_download_calls += sample.call_count;
    precise_readback_copy_count += sample.copy_count;
    precise_readback_downloaded_bytes += sample.bytes;
    precise_readback_no_downloads += sample.bytes == 0;
    precise_readback_finish_nanoseconds += sample.finish_nanoseconds;
    precise_readback_gpu_copy_nanoseconds += sample.gpu_copy_nanoseconds;
    precise_readback_gpu_timestamp_samples += sample.gpu_timestamp_samples;
    precise_readback_gpu_timestamp_failures += sample.gpu_timestamp_failures;
    precise_readback_prior_wait_nanoseconds += sample.prior_wait_nanoseconds;
    precise_readback_submit_nanoseconds += sample.submit_nanoseconds;
    precise_readback_current_wait_nanoseconds += sample.current_wait_nanoseconds;
    precise_readback_wait_nanoseconds += sample.wait_nanoseconds;
    precise_readback_max_finish_nanoseconds =
        std::max(precise_readback_max_finish_nanoseconds, sample.finish_nanoseconds);
    precise_readback_write_site_window_hits +=
        precise_readback_write_site_pc != 0 && is_write &&
        fault_context.rip == precise_readback_write_site_pc &&
        request_window_size == precise_readback_write_site_window_size;
    precise_readback_write_discard_coverage.selector_hits += coverage.selector_hits;
    precise_readback_write_discard_coverage.valid_contexts += coverage.valid_contexts;
    precise_readback_write_discard_coverage.write_span_bytes += coverage.write_span_bytes;
    precise_readback_write_discard_coverage.fault_page_write_bytes +=
        coverage.fault_page_write_bytes;
    precise_readback_write_discard_coverage.gpu_dirty_bytes += coverage.gpu_dirty_bytes;
    precise_readback_write_discard_coverage.covered_dirty_bytes += coverage.covered_dirty_bytes;
    precise_readback_write_discard_coverage.fully_covered_requests +=
        coverage.fully_covered_requests;
    precise_readback_write_discard_coverage.zero_dirty_requests += coverage.zero_dirty_requests;

    const VAddr page_address = Common::AlignDown(device_addr, ReadbackStatsPageSize);
    auto page = std::ranges::find_if(
        precise_readback_hot_pages, [page_address](const ReadbackHotPage& candidate) {
            return candidate.valid && candidate.address == page_address;
        });
    if (page != precise_readback_hot_pages.end()) {
        precise_readback_bounded_repeats++;
    } else {
        page =
            std::ranges::find_if(precise_readback_hot_pages,
                                 [](const ReadbackHotPage& candidate) { return !candidate.valid; });
        if (page == precise_readback_hot_pages.end()) {
            page = std::ranges::min_element(precise_readback_hot_pages, {},
                                            &ReadbackHotPage::last_request);
        }
        *page = ReadbackHotPage{
            .address = page_address,
            .last_request = precise_readback_sequence,
            .valid = true,
        };
    }
    page->last_request = precise_readback_sequence;
    page->total_requests++;
    page->interval_requests++;
    page->interval_writes += is_write;

    if (fault_context.rip != 0) {
        auto site = std::ranges::find_if(
            precise_readback_hot_fault_sites,
            [&fault_context, page_address](const ReadbackHotFaultSite& candidate) {
                return candidate.valid && candidate.fault_pc == fault_context.rip &&
                       candidate.page_address == page_address;
            });
        if (site == precise_readback_hot_fault_sites.end()) {
            site = std::ranges::find_if(
                precise_readback_hot_fault_sites,
                [](const ReadbackHotFaultSite& candidate) { return !candidate.valid; });
        }
        if (site == precise_readback_hot_fault_sites.end()) {
            site = std::ranges::min_element(precise_readback_hot_fault_sites, {},
                                            &ReadbackHotFaultSite::last_request);
        }
        if (!site->valid || site->fault_pc != fault_context.rip ||
            site->page_address != page_address) {
            *site = ReadbackHotFaultSite{
                .fault_pc = fault_context.rip,
                .page_address = page_address,
                .last_context = fault_context,
                .min_rdx = fault_context.rdx,
                .max_rdx = fault_context.rdx,
                .min_rcx = fault_context.rcx,
                .max_rcx = fault_context.rcx,
                .valid = true,
            };
        }
        site->last_context = fault_context;
        if (site->interval_requests == 0) {
            site->min_rdx = fault_context.rdx;
            site->max_rdx = fault_context.rdx;
            site->min_rcx = fault_context.rcx;
            site->max_rcx = fault_context.rcx;
        } else {
            site->min_rdx = std::min(site->min_rdx, fault_context.rdx);
            site->max_rdx = std::max(site->max_rdx, fault_context.rdx);
            site->min_rcx = std::min(site->min_rcx, fault_context.rcx);
            site->max_rcx = std::max(site->max_rcx, fault_context.rcx);
        }
        site->last_request = precise_readback_sequence;
        site->interval_requests++;
        site->interval_writes += is_write;
    }

    if (precise_readback_requests >= precise_readback_stats_interval) {
        LogPreciseReadbackStats();
    }
}

void BufferCache::RecordPreciseReadbackBufferStats(const Buffer& buffer, bool is_write,
                                                   const ReadbackDownloadSample& sample) {
    const VAddr buffer_address = buffer.CpuAddr();
    const u64 buffer_size = buffer.SizeBytes();
    auto contribution = std::ranges::find_if(
        precise_readback_buffer_contributions,
        [buffer_address, buffer_size](const ReadbackBufferContribution& candidate) {
            return candidate.valid && candidate.address == buffer_address &&
                   candidate.size_bytes == buffer_size;
        });
    if (contribution == precise_readback_buffer_contributions.end()) {
        contribution = std::ranges::find_if(
            precise_readback_buffer_contributions,
            [](const ReadbackBufferContribution& candidate) { return !candidate.valid; });
        if (contribution == precise_readback_buffer_contributions.end()) {
            precise_readback_buffer_table_drops++;
            return;
        }
        *contribution = ReadbackBufferContribution{
            .address = buffer_address,
            .size_bytes = buffer_size,
            .valid = true,
        };
    }
    contribution->interval_requests++;
    contribution->interval_writes += is_write;
    contribution->download_calls += sample.call_count;
    contribution->copy_count += sample.copy_count;
    contribution->downloaded_bytes += sample.bytes;
    contribution->finish_nanoseconds += sample.finish_nanoseconds;
    contribution->submit_nanoseconds += sample.submit_nanoseconds;
    contribution->wait_nanoseconds += sample.wait_nanoseconds;
}

void BufferCache::LogPreciseReadbackStats() {
    const u64 interval_finished_nanoseconds = SteadyClockNanoseconds();
    const u64 interval_wall_nanoseconds =
        interval_finished_nanoseconds - precise_readback_interval_started_nanoseconds;
    std::array<const ReadbackHotPage*, 3> hottest{};
    std::array<const ReadbackHotFaultSite*, 3> hottest_sites{};
    std::array<const ReadbackBufferContribution*, 3> hottest_buffers{};
    std::array<const ReadbackBufferContribution*, 3> slowest_buffers{};
    u64 tracked_pages = 0;
    u64 tracked_buffers = 0;
    for (const auto& page : precise_readback_hot_pages) {
        if (!page.valid || page.interval_requests == 0) {
            continue;
        }
        tracked_pages++;
        for (size_t index = 0; index < hottest.size(); ++index) {
            if (hottest[index] == nullptr ||
                page.interval_requests > hottest[index]->interval_requests) {
                for (size_t shift = hottest.size() - 1; shift > index; --shift) {
                    hottest[shift] = hottest[shift - 1];
                }
                hottest[index] = &page;
                break;
            }
        }
    }
    for (const auto& site : precise_readback_hot_fault_sites) {
        if (!site.valid || site.interval_requests == 0) {
            continue;
        }
        for (size_t index = 0; index < hottest_sites.size(); ++index) {
            if (hottest_sites[index] == nullptr ||
                site.interval_requests > hottest_sites[index]->interval_requests) {
                for (size_t shift = hottest_sites.size() - 1; shift > index; --shift) {
                    hottest_sites[shift] = hottest_sites[shift - 1];
                }
                hottest_sites[index] = &site;
                break;
            }
        }
    }
    for (const auto& contribution : precise_readback_buffer_contributions) {
        if (!contribution.valid || contribution.interval_requests == 0) {
            continue;
        }
        tracked_buffers++;
        for (size_t index = 0; index < hottest_buffers.size(); ++index) {
            if (hottest_buffers[index] == nullptr ||
                contribution.downloaded_bytes > hottest_buffers[index]->downloaded_bytes) {
                for (size_t shift = hottest_buffers.size() - 1; shift > index; --shift) {
                    hottest_buffers[shift] = hottest_buffers[shift - 1];
                }
                hottest_buffers[index] = &contribution;
                break;
            }
        }
        for (size_t index = 0; index < slowest_buffers.size(); ++index) {
            if (slowest_buffers[index] == nullptr ||
                contribution.finish_nanoseconds > slowest_buffers[index]->finish_nanoseconds) {
                for (size_t shift = slowest_buffers.size() - 1; shift > index; --shift) {
                    slowest_buffers[shift] = slowest_buffers[shift - 1];
                }
                slowest_buffers[index] = &contribution;
                break;
            }
        }
    }
    const ReadbackHotPage empty{};
    const auto& first = hottest[0] != nullptr ? *hottest[0] : empty;
    const auto& second = hottest[1] != nullptr ? *hottest[1] : empty;
    const auto& third = hottest[2] != nullptr ? *hottest[2] : empty;
    const ReadbackHotFaultSite empty_site{};
    const auto& first_site = hottest_sites[0] != nullptr ? *hottest_sites[0] : empty_site;
    const auto& second_site = hottest_sites[1] != nullptr ? *hottest_sites[1] : empty_site;
    const auto& third_site = hottest_sites[2] != nullptr ? *hottest_sites[2] : empty_site;
    const ReadbackBufferContribution empty_buffer{};
    const auto& first_buffer = hottest_buffers[0] != nullptr ? *hottest_buffers[0] : empty_buffer;
    const auto& second_buffer = hottest_buffers[1] != nullptr ? *hottest_buffers[1] : empty_buffer;
    const auto& third_buffer = hottest_buffers[2] != nullptr ? *hottest_buffers[2] : empty_buffer;
    const auto& first_slow_buffer =
        slowest_buffers[0] != nullptr ? *slowest_buffers[0] : empty_buffer;
    const auto& second_slow_buffer =
        slowest_buffers[1] != nullptr ? *slowest_buffers[1] : empty_buffer;
    const auto& third_slow_buffer =
        slowest_buffers[2] != nullptr ? *slowest_buffers[2] : empty_buffer;
    const double finish_total_ms =
        static_cast<double>(precise_readback_finish_nanoseconds) / 1'000'000.0;
    const double finish_average_ms =
        finish_total_ms / static_cast<double>(precise_readback_requests);
    const double finish_max_ms =
        static_cast<double>(precise_readback_max_finish_nanoseconds) / 1'000'000.0;
    const double submit_total_ms =
        static_cast<double>(precise_readback_submit_nanoseconds) / 1'000'000.0;
    const double prior_wait_total_ms =
        static_cast<double>(precise_readback_prior_wait_nanoseconds) / 1'000'000.0;
    const double current_wait_total_ms =
        static_cast<double>(precise_readback_current_wait_nanoseconds) / 1'000'000.0;
    const double gpu_copy_total_ms =
        static_cast<double>(precise_readback_gpu_copy_nanoseconds) / 1'000'000.0;
    const double wait_total_ms =
        static_cast<double>(precise_readback_wait_nanoseconds) / 1'000'000.0;
    const double submit_share = precise_readback_finish_nanoseconds != 0
                                    ? static_cast<double>(precise_readback_submit_nanoseconds) *
                                          100.0 /
                                          static_cast<double>(precise_readback_finish_nanoseconds)
                                    : 0.0;
    const double wait_share = precise_readback_finish_nanoseconds != 0
                                  ? static_cast<double>(precise_readback_wait_nanoseconds) * 100.0 /
                                        static_cast<double>(precise_readback_finish_nanoseconds)
                                  : 0.0;
    const double prior_wait_share = precise_readback_finish_nanoseconds != 0
                                        ? static_cast<double>(
                                              precise_readback_prior_wait_nanoseconds) *
                                              100.0 /
                                              static_cast<double>(
                                                  precise_readback_finish_nanoseconds)
                                        : 0.0;
    const double current_wait_share = precise_readback_finish_nanoseconds != 0
                                          ? static_cast<double>(
                                                precise_readback_current_wait_nanoseconds) *
                                                100.0 /
                                                static_cast<double>(
                                                    precise_readback_finish_nanoseconds)
                                          : 0.0;
    const double gpu_copy_share_of_current_wait =
        precise_readback_current_wait_nanoseconds != 0
            ? static_cast<double>(precise_readback_gpu_copy_nanoseconds) * 100.0 /
                  static_cast<double>(precise_readback_current_wait_nanoseconds)
            : 0.0;
    const double average_outstanding_depth =
        static_cast<double>(precise_readback_outstanding_depth_sum) /
        static_cast<double>(precise_readback_requests);
    const double wall_ms = static_cast<double>(interval_wall_nanoseconds) / 1'000'000.0;
    const double requests_per_second = interval_wall_nanoseconds != 0
                                           ? static_cast<double>(precise_readback_requests) *
                                                 1'000'000'000.0 /
                                                 static_cast<double>(interval_wall_nanoseconds)
                                           : 0.0;
    const double finish_share = interval_wall_nanoseconds != 0
                                    ? static_cast<double>(precise_readback_finish_nanoseconds) *
                                          100.0 / static_cast<double>(interval_wall_nanoseconds)
                                    : 0.0;
    const double amplification = precise_readback_requested_bytes != 0
                                     ? static_cast<double>(precise_readback_downloaded_bytes) /
                                           static_cast<double>(precise_readback_requested_bytes)
                                     : 0.0;
    LOG_INFO(
        Render_Vulkan,
        "Precise readback stats: window_kib={} requests={} writes={} reads={} "
        "bounded_repeats={} "
        "tracked_pages={} requested_bytes={} download_calls={} copies={} downloaded_bytes={} "
        "no_downloads={} finish_total_ms={:.3f} finish_avg_ms={:.3f} finish_max_ms={:.3f} "
        "submit_total_ms={:.3f} wait_total_ms={:.3f} submit_share_pct={:.1f} "
        "wait_share_pct={:.1f} phase_split={} prior_wait_total_ms={:.3f} "
        "current_wait_total_ms={:.3f} prior_wait_share_pct={:.1f} "
        "current_wait_share_pct={:.1f} gpu_timestamp={} gpu_timestamp_bits={} "
        "gpu_timestamp_period_ns={:.3f} gpu_timestamp_samples={} "
        "gpu_timestamp_failures={} gpu_copy_total_ms={:.3f} "
        "gpu_copy_share_current_pct={:.3f} queued_requests={} avg_outstanding_depth={:.2f} "
        "max_outstanding_depth={} wall_ms={:.3f} request_rate={:.1f} "
        "finish_share_pct={:.1f} site_window_kib={} site_window_hits={} "
        "discard_probe_hits={} discard_probe_valid={} discard_write_span_bytes={} "
        "discard_page_write_bytes={} discard_dirty_bytes={} discard_covered_bytes={} "
        "discard_full_requests={} discard_zero_dirty_requests={} "
        "tracked_buffers={} buffer_table_drops={} "
        "hot_buffers=[{:#x}+{}:{}r/{}w/{}d/{}c/{}b/{:.3f}ms, "
        "{:#x}+{}:{}r/{}w/{}d/{}c/{}b/{:.3f}ms, "
        "{:#x}+{}:{}r/{}w/{}d/{}c/{}b/{:.3f}ms] "
        "slow_buffers=[{:#x}+{}:{}r/{}w/{}d/{}c/{}b/{:.3f}ms, "
        "{:#x}+{}:{}r/{}w/{}d/{}c/{}b/{:.3f}ms, "
        "{:#x}+{}:{}r/{}w/{}d/{}c/{}b/{:.3f}ms] "
        "amplification={:.1f}x hot=[{:#x}:{}(w{}), {:#x}:{}(w{}), {:#x}:{}(w{})] "
        "hot_sites=[{:#x}@{:#x}:{}(w{}), {:#x}@{:#x}:{}(w{}), "
        "{:#x}@{:#x}:{}(w{})] "
        "top_context=[{:#x}@{:#x}:{}(w{});rax:{:#x};rcx:{:#x};rdx:{:#x};"
        "rsi:{:#x};rdi:{:#x};rbp:{:#x};rsp:{:#x};rcx_range:{:#x}-{:#x};"
        "rdx_range:{:#x}-{:#x}]",
        precise_readback_window_size / 1_KB, precise_readback_requests, precise_readback_writes,
        precise_readback_requests - precise_readback_writes, precise_readback_bounded_repeats,
        tracked_pages, precise_readback_requested_bytes, precise_readback_download_calls,
        precise_readback_copy_count, precise_readback_downloaded_bytes,
        precise_readback_no_downloads, finish_total_ms, finish_average_ms, finish_max_ms,
        submit_total_ms, wait_total_ms, submit_share, wait_share,
        static_cast<u64>(precise_readback_phase_timing_enabled), prior_wait_total_ms,
        current_wait_total_ms, prior_wait_share, current_wait_share,
        static_cast<u64>(static_cast<bool>(precise_readback_gpu_timestamp_pool)),
        precise_readback_gpu_timestamp_valid_bits, precise_readback_gpu_timestamp_period_ns,
        precise_readback_gpu_timestamp_samples, precise_readback_gpu_timestamp_failures,
        gpu_copy_total_ms, gpu_copy_share_of_current_wait, precise_readback_queued_requests,
        average_outstanding_depth, precise_readback_max_outstanding_depth, wall_ms,
        requests_per_second, finish_share, precise_readback_write_site_window_size / 1_KB,
        precise_readback_write_site_window_hits,
        precise_readback_write_discard_coverage.selector_hits,
        precise_readback_write_discard_coverage.valid_contexts,
        precise_readback_write_discard_coverage.write_span_bytes,
        precise_readback_write_discard_coverage.fault_page_write_bytes,
        precise_readback_write_discard_coverage.gpu_dirty_bytes,
        precise_readback_write_discard_coverage.covered_dirty_bytes,
        precise_readback_write_discard_coverage.fully_covered_requests,
        precise_readback_write_discard_coverage.zero_dirty_requests, tracked_buffers,
        precise_readback_buffer_table_drops, first_buffer.address, first_buffer.size_bytes,
        first_buffer.interval_requests, first_buffer.interval_writes, first_buffer.download_calls,
        first_buffer.copy_count, first_buffer.downloaded_bytes,
        static_cast<double>(first_buffer.finish_nanoseconds) / 1'000'000.0, second_buffer.address,
        second_buffer.size_bytes, second_buffer.interval_requests, second_buffer.interval_writes,
        second_buffer.download_calls, second_buffer.copy_count, second_buffer.downloaded_bytes,
        static_cast<double>(second_buffer.finish_nanoseconds) / 1'000'000.0, third_buffer.address,
        third_buffer.size_bytes, third_buffer.interval_requests, third_buffer.interval_writes,
        third_buffer.download_calls, third_buffer.copy_count, third_buffer.downloaded_bytes,
        static_cast<double>(third_buffer.finish_nanoseconds) / 1'000'000.0,
        first_slow_buffer.address, first_slow_buffer.size_bytes,
        first_slow_buffer.interval_requests, first_slow_buffer.interval_writes,
        first_slow_buffer.download_calls, first_slow_buffer.copy_count,
        first_slow_buffer.downloaded_bytes,
        static_cast<double>(first_slow_buffer.finish_nanoseconds) / 1'000'000.0,
        second_slow_buffer.address, second_slow_buffer.size_bytes,
        second_slow_buffer.interval_requests, second_slow_buffer.interval_writes,
        second_slow_buffer.download_calls, second_slow_buffer.copy_count,
        second_slow_buffer.downloaded_bytes,
        static_cast<double>(second_slow_buffer.finish_nanoseconds) / 1'000'000.0,
        third_slow_buffer.address, third_slow_buffer.size_bytes,
        third_slow_buffer.interval_requests, third_slow_buffer.interval_writes,
        third_slow_buffer.download_calls, third_slow_buffer.copy_count,
        third_slow_buffer.downloaded_bytes,
        static_cast<double>(third_slow_buffer.finish_nanoseconds) / 1'000'000.0, amplification,
        first.address, first.interval_requests, first.interval_writes, second.address,
        second.interval_requests, second.interval_writes, third.address, third.interval_requests,
        third.interval_writes, first_site.fault_pc, first_site.page_address,
        first_site.interval_requests, first_site.interval_writes, second_site.fault_pc,
        second_site.page_address, second_site.interval_requests, second_site.interval_writes,
        third_site.fault_pc, third_site.page_address, third_site.interval_requests,
        third_site.interval_writes, first_site.fault_pc, first_site.page_address,
        first_site.interval_requests, first_site.interval_writes, first_site.last_context.rax,
        first_site.last_context.rcx, first_site.last_context.rdx, first_site.last_context.rsi,
        first_site.last_context.rdi, first_site.last_context.rbp, first_site.last_context.rsp,
        first_site.min_rcx, first_site.max_rcx, first_site.min_rdx, first_site.max_rdx);

    precise_readback_requests = 0;
    precise_readback_queued_requests = 0;
    precise_readback_outstanding_depth_sum = 0;
    precise_readback_max_outstanding_depth = 0;
    precise_readback_writes = 0;
    precise_readback_requested_bytes = 0;
    precise_readback_bounded_repeats = 0;
    precise_readback_download_calls = 0;
    precise_readback_copy_count = 0;
    precise_readback_downloaded_bytes = 0;
    precise_readback_no_downloads = 0;
    precise_readback_finish_nanoseconds = 0;
    precise_readback_gpu_copy_nanoseconds = 0;
    precise_readback_gpu_timestamp_samples = 0;
    precise_readback_gpu_timestamp_failures = 0;
    precise_readback_prior_wait_nanoseconds = 0;
    precise_readback_submit_nanoseconds = 0;
    precise_readback_current_wait_nanoseconds = 0;
    precise_readback_wait_nanoseconds = 0;
    precise_readback_max_finish_nanoseconds = 0;
    precise_readback_write_site_window_hits = 0;
    precise_readback_buffer_table_drops = 0;
    precise_readback_write_discard_coverage = {};
    precise_readback_interval_started_nanoseconds = interval_finished_nanoseconds;
    for (auto& page : precise_readback_hot_pages) {
        page.interval_requests = 0;
        page.interval_writes = 0;
    }
    for (auto& site : precise_readback_hot_fault_sites) {
        site.interval_requests = 0;
        site.interval_writes = 0;
    }
    precise_readback_buffer_contributions = {};
}

BufferCache::ReadbackDownloadSample BufferCache::DownloadBufferMemory(Buffer& buffer,
                                                                      VAddr device_addr, u64 size,
                                                                      bool measure_finish) {
    ReadbackDownloadSample sample{};
    boost::container::small_vector<vk::BufferCopy, 1> copies;
    u64 total_size_bytes = 0;
    memory_tracker->ForEachDownloadRange<false>(
        device_addr, size, [&](u64 device_addr_out, u64 range_size) {
            const VAddr buffer_addr = buffer.CpuAddr();
            const auto add_download = [&](VAddr start, VAddr end) {
                const u64 new_offset = start - buffer_addr;
                const u64 new_size = end - start;
                copies.push_back(vk::BufferCopy{
                    .srcOffset = new_offset,
                    .dstOffset = total_size_bytes,
                    .size = new_size,
                });
                // Align up to avoid cache conflicts
                constexpr u64 align = 64ULL;
                constexpr u64 mask = ~(align - 1ULL);
                total_size_bytes += (new_size + align - 1) & mask;
            };
            gpu_modified_ranges.ForEachInRange(device_addr_out, range_size, add_download);
            gpu_modified_ranges.Subtract(device_addr_out, range_size);
        });
    if (total_size_bytes == 0) {
        return sample;
    }
    sample.bytes = total_size_bytes;
    sample.call_count = 1;
    sample.copy_count = copies.size();
    const auto [download, offset] = download_buffer.Map(total_size_bytes);
    ASSERT_MSG(download != nullptr, "Buffer download of {} bytes exceeds the {} byte readback ring",
               total_size_bytes, DownloadBufferSize);
    for (auto& copy : copies) {
        // Modify copies to have the staging offset in mind
        copy.dstOffset += offset;
    }
    download_buffer.Commit();
    scheduler.EndRendering();
    const auto cmdbuf = scheduler.CommandBuffer();
    const bool measure_gpu_copy =
        measure_finish && static_cast<bool>(precise_readback_gpu_timestamp_pool);
    if (measure_gpu_copy) {
        cmdbuf.resetQueryPool(*precise_readback_gpu_timestamp_pool, 0, 2);
        cmdbuf.writeTimestamp2(vk::PipelineStageFlagBits2::eAllCommands,
                               *precise_readback_gpu_timestamp_pool, 0);
    }
    // Synchronize prior GPU writes to this buffer before the transfer read
    const vk::BufferMemoryBarrier2 pre_barrier = {
        .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eTransfer,
        .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
        .buffer = buffer.buffer,
        .offset = 0,
        .size = buffer.SizeBytes(),
    };
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = 1,
        .pBufferMemoryBarriers = &pre_barrier,
    });
    cmdbuf.copyBuffer(buffer.buffer, download_buffer.Handle(), copies);
    if (measure_gpu_copy) {
        cmdbuf.writeTimestamp2(vk::PipelineStageFlagBits2::eAllCommands,
                               *precise_readback_gpu_timestamp_pool, 1);
    }
    const VAddr buffer_addr = buffer.CpuAddr();
    const auto write_data = [this, copies = std::move(copies), buffer_addr, device_addr, size,
                             download, offset, total_size_bytes]() {
        if (!download_buffer.is_coherent) {
            vmaInvalidateAllocation(instance.GetAllocator(), download_buffer.buffer.allocation,
                                    offset, total_size_bytes);
        }
        auto* memory = Core::Memory::Instance();
        for (const auto& copy : copies) {
            const VAddr copy_device_addr = buffer_addr + copy.srcOffset;
            const u64 dst_offset = copy.dstOffset - offset;
            memory->TryWriteBacking(std::bit_cast<u8*>(copy_device_addr), download + dst_offset,
                                    copy.size);
        }
        memory_tracker->UnmarkRegionAsGpuModified(device_addr, size);
    };
    const auto finish_start =
        measure_finish ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
    if (measure_finish) {
        const auto timing = scheduler.FinishWithTiming(precise_readback_phase_timing_enabled);
        sample.prior_wait_nanoseconds = timing.prior_wait_nanoseconds;
        sample.submit_nanoseconds = timing.submit_nanoseconds;
        sample.current_wait_nanoseconds = timing.current_wait_nanoseconds;
        sample.wait_nanoseconds = timing.wait_nanoseconds;
    } else {
        scheduler.Finish();
    }
    if (measure_finish) {
        sample.finish_nanoseconds =
            static_cast<u64>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                 std::chrono::steady_clock::now() - finish_start)
                                 .count());
    }
    if (measure_gpu_copy) {
        std::array<u64, 2> timestamps{};
        const auto result = instance.GetDevice().getQueryPoolResults(
            *precise_readback_gpu_timestamp_pool, 0, timestamps.size(), sizeof(timestamps),
            timestamps.data(), sizeof(u64),
            vk::QueryResultFlagBits::e64 | vk::QueryResultFlagBits::eWait);
        if (result == vk::Result::eSuccess) {
            u64 delta_ticks = timestamps[1] - timestamps[0];
            if (precise_readback_gpu_timestamp_valid_bits < 64) {
                const u64 valid_mask = (u64{1} << precise_readback_gpu_timestamp_valid_bits) - 1;
                delta_ticks &= valid_mask;
            }
            const long double gpu_copy_nanoseconds =
                static_cast<long double>(delta_ticks) *
                static_cast<long double>(precise_readback_gpu_timestamp_period_ns);
            if (gpu_copy_nanoseconds <= static_cast<long double>(std::numeric_limits<u64>::max())) {
                sample.gpu_copy_nanoseconds = static_cast<u64>(gpu_copy_nanoseconds);
                sample.gpu_timestamp_samples = 1;
            } else {
                sample.gpu_timestamp_failures = 1;
            }
        } else {
            sample.gpu_timestamp_failures = 1;
        }
    }
    write_data();
    return sample;
}

void BufferCache::BindVertexBuffers(
    const Vulkan::GraphicsPipeline& pipeline,
    boost::container::small_vector<vk::BufferMemoryBarrier2, 16>& barriers) {
    const auto& regs = liverpool->regs;
    Vulkan::VertexInputs<vk::VertexInputAttributeDescription2EXT> attributes;
    Vulkan::VertexInputs<vk::VertexInputBindingDescription2EXT> bindings;
    Vulkan::VertexInputs<vk::VertexInputBindingDivisorDescriptionEXT> divisors;
    Vulkan::VertexInputs<AmdGpu::Buffer> guest_buffers;
    pipeline.GetVertexInputs(attributes, bindings, divisors, guest_buffers,
                             regs.vgt_instance_step_rate_0, regs.vgt_instance_step_rate_1);

    if (instance.IsVertexInputDynamicState()) {
        // Update current vertex inputs.
        const auto cmdbuf = scheduler.CommandBuffer();
        cmdbuf.setVertexInputEXT(bindings, attributes);
    }

    if (bindings.empty()) {
        // If there are no bindings, there is nothing further to do.
        return;
    }

    struct BufferRange {
        VAddr base_address;
        VAddr end_address;
        vk::Buffer vk_buffer;
        u64 offset;

        [[nodiscard]] size_t GetSize() const {
            return end_address - base_address;
        }
    };

    // Build list of ranges covering the requested buffers
    Vulkan::VertexInputs<BufferRange> ranges{};
    for (const auto& buffer : guest_buffers) {
        if (buffer.base_address != 0 && buffer.GetSize() > 0) {
            ranges.emplace_back(buffer.base_address, buffer.base_address + buffer.GetSize());
        }
    }

    // Merge connecting ranges together
    Vulkan::VertexInputs<BufferRange> ranges_merged{};
    if (!ranges.empty()) {
        std::ranges::sort(ranges, [](const BufferRange& lhv, const BufferRange& rhv) {
            return lhv.base_address < rhv.base_address;
        });
        ranges_merged.emplace_back(ranges[0]);
        for (auto range : ranges) {
            auto& prev_range = ranges_merged.back();
            if (prev_range.end_address < range.base_address) {
                ranges_merged.emplace_back(range);
            } else {
                prev_range.end_address = std::max(prev_range.end_address, range.end_address);
            }
        }
    }

    // Map buffers for merged ranges
    for (auto& range : ranges_merged) {
        const u64 size = memory->ClampRangeSize(range.base_address, range.GetSize());
        const auto [buffer, offset] = ObtainBuffer(range.base_address, size, false);
        range.vk_buffer = buffer->buffer;
        range.offset = offset;
        if (IsRegionGpuModified(range.base_address, size)) {
            if (auto barrier =
                    buffer->GetBarrier(vk::AccessFlagBits2::eVertexAttributeRead,
                                       vk::PipelineStageFlagBits2::eVertexAttributeInput)) {
                barriers.emplace_back(*barrier);
            }
        }
    }

    // Bind vertex buffers
    Vulkan::VertexInputs<vk::Buffer> host_buffers;
    Vulkan::VertexInputs<vk::DeviceSize> host_offsets;
    Vulkan::VertexInputs<vk::DeviceSize> host_sizes;
    Vulkan::VertexInputs<vk::DeviceSize> host_strides;
    for (const auto& buffer : guest_buffers) {
        if (buffer.base_address != 0 && buffer.GetSize() > 0) {
            const auto host_buffer_info =
                std::ranges::find_if(ranges_merged, [&](const BufferRange& range) {
                    return buffer.base_address >= range.base_address &&
                           buffer.base_address < range.end_address;
                });
            ASSERT(host_buffer_info != ranges_merged.cend());
            host_buffers.emplace_back(host_buffer_info->vk_buffer);
            host_offsets.push_back(host_buffer_info->offset + buffer.base_address -
                                   host_buffer_info->base_address);
        } else {
            host_buffers.emplace_back(VK_NULL_HANDLE);
            host_offsets.push_back(0);
        }
        host_sizes.push_back(buffer.GetSize());
        host_strides.push_back(buffer.GetStride());
    }

    const auto cmdbuf = scheduler.CommandBuffer();
    const auto num_buffers = guest_buffers.size();
    if (instance.IsVertexInputDynamicState()) {
        cmdbuf.bindVertexBuffers(0, num_buffers, host_buffers.data(), host_offsets.data());
    } else {
        cmdbuf.bindVertexBuffers2(0, num_buffers, host_buffers.data(), host_offsets.data(),
                                  host_sizes.data(), host_strides.data());
    }
}

void BufferCache::BindIndexBuffer(
    u32 index_offset, boost::container::small_vector<vk::BufferMemoryBarrier2, 16>& barriers) {
    const auto& regs = liverpool->regs;

    // Figure out index type and size.
    const bool is_index16 = regs.index_buffer_type.index_type == AmdGpu::IndexType::Index16;
    const vk::IndexType index_type = is_index16 ? vk::IndexType::eUint16 : vk::IndexType::eUint32;
    const u32 index_size = is_index16 ? sizeof(u16) : sizeof(u32);
    const VAddr index_address =
        regs.index_base_address.Address<VAddr>() + index_offset * index_size;

    // Bind index buffer.
    const u32 index_buffer_size = regs.num_indices * index_size;
    const auto [vk_buffer, offset] = ObtainBuffer(index_address, index_buffer_size, false);
    if (IsRegionGpuModified(index_address, index_buffer_size)) {
        if (auto barrier = vk_buffer->GetBarrier(vk::AccessFlagBits2::eIndexRead,
                                                 vk::PipelineStageFlagBits2::eIndexInput)) {
            barriers.emplace_back(*barrier);
        }
    }
    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.bindIndexBuffer(vk_buffer->Handle(), offset, index_type);
}

void BufferCache::FillBuffer(VAddr address, u32 num_bytes, u32 value, bool is_gds) {
    ASSERT_MSG(address % 4 == 0, "GDS offset must be dword aligned");
    if (!is_gds) {
        texture_cache.ClearMeta(address);
        if (!IsRegionGpuModified(address, num_bytes)) {
            u32* buffer = std::bit_cast<u32*>(address);
            std::fill(buffer, buffer + num_bytes / sizeof(u32), value);
            return;
        }
    }
    Buffer* buffer = [&] {
        if (is_gds) {
            return &gds_buffer;
        }
        const auto [buffer, offset] = ObtainBuffer(address, num_bytes, true);
        return buffer;
    }();
    buffer->Fill(buffer->Offset(address), num_bytes, value);
}

void BufferCache::CopyBuffer(VAddr dst, VAddr src, u32 num_bytes, bool dst_gds, bool src_gds) {
    if (!dst_gds && !IsRegionGpuModified(dst, num_bytes)) {
        if (!src_gds && !IsRegionGpuModified(src, num_bytes) &&
            !texture_cache.FindImageFromRange(src, num_bytes)) {
            // Both buffers were not transferred to GPU yet. Can safely copy in host memory.
            memcpy(std::bit_cast<void*>(dst), std::bit_cast<void*>(src), num_bytes);
            return;
        }
        // Without a readback there's nothing we can do with this
        // Fallback to creating dst buffer on GPU to at least have this data there
    }
    texture_cache.InvalidateMemoryFromGPU(dst, num_bytes);
    auto& src_buffer = [&] -> const Buffer& {
        if (src_gds) {
            return gds_buffer;
        }
        const auto buffer_id = FindBuffer(src, num_bytes);
        auto& buffer = slot_buffers[buffer_id];
        SynchronizeBuffer(buffer, src, num_bytes, false, true);
        return buffer;
    }();
    auto& dst_buffer = [&] -> const Buffer& {
        if (dst_gds) {
            return gds_buffer;
        }
        const auto buffer_id = FindBuffer(dst, num_bytes);
        auto& buffer = slot_buffers[buffer_id];
        SynchronizeBuffer(buffer, dst, num_bytes, true, true);
        gpu_modified_ranges.Add(dst, num_bytes);
        return buffer;
    }();
    const vk::BufferCopy region = {
        .srcOffset = src_buffer.Offset(src),
        .dstOffset = dst_buffer.Offset(dst),
        .size = num_bytes,
    };
    const vk::BufferMemoryBarrier2 buf_barriers_before[2] = {
        {
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryRead,
            .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .dstAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .buffer = dst_buffer.Handle(),
            .offset = dst_buffer.Offset(dst),
            .size = num_bytes,
        },
        {
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
            .buffer = src_buffer.Handle(),
            .offset = src_buffer.Offset(src),
            .size = num_bytes,
        },
    };
    scheduler.EndRendering();
    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = 2,
        .pBufferMemoryBarriers = buf_barriers_before,
    });
    cmdbuf.copyBuffer(src_buffer.Handle(), dst_buffer.Handle(), region);
    const vk::BufferMemoryBarrier2 buf_barriers_after[2] = {
        {
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .dstAccessMask = vk::AccessFlagBits2::eMemoryRead,
            .buffer = dst_buffer.Handle(),
            .offset = dst_buffer.Offset(dst),
            .size = num_bytes,
        },
        {
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eTransferRead,
            .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .dstAccessMask = vk::AccessFlagBits2::eMemoryWrite,
            .buffer = src_buffer.Handle(),
            .offset = src_buffer.Offset(src),
            .size = num_bytes,
        },
    };
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = 2,
        .pBufferMemoryBarriers = buf_barriers_after,
    });
}

std::pair<Buffer*, u32> BufferCache::ObtainBuffer(VAddr device_addr, u32 size, bool is_written,
                                                  bool is_texel_buffer, BufferId buffer_id) {
    // For read-only buffers use device local stream buffer to reduce renderpass breaks.
    if (!is_written && size <= CACHING_PAGESIZE && !IsRegionGpuModified(device_addr, size)) {
        const u64 offset = stream_buffer.Copy(device_addr, size, instance.UniformMinAlignment());
        return {&stream_buffer, offset};
    }
    if (IsBufferInvalid(buffer_id)) {
        buffer_id = FindBuffer(device_addr, size);
    }
    Buffer& buffer = slot_buffers[buffer_id];
    SynchronizeBuffer(buffer, device_addr, size, is_written, is_texel_buffer);
    if (is_written) {
        gpu_modified_ranges.Add(device_addr, size);
    }
    return {&buffer, buffer.Offset(device_addr)};
}

std::pair<Buffer*, u32> BufferCache::ObtainBufferForImage(VAddr gpu_addr, u32 size) {
    // Check if any buffer contains the full requested range.
    const BufferId buffer_id = page_table[gpu_addr >> CACHING_PAGEBITS].buffer_id;
    if (buffer_id) {
        if (Buffer& buffer = slot_buffers[buffer_id]; buffer.IsInBounds(gpu_addr, size)) {
            SynchronizeBuffer(buffer, gpu_addr, size, false, false);
            return {&buffer, buffer.Offset(gpu_addr)};
        }
    }
    // If some buffer within was GPU modified create a full buffer to avoid losing GPU data.
    if (IsRegionGpuModified(gpu_addr, size)) {
        return ObtainBuffer(gpu_addr, size, false, false);
    }
    // In all other cases, just do a CPU copy to the staging buffer.
    const auto [data, offset] = staging_buffer.Map(size, instance.StorageMinAlignment());
    memory->CopySparseMemory(gpu_addr, data, size);
    staging_buffer.Commit();
    return {&staging_buffer, offset};
}

bool BufferCache::IsRegionRegistered(VAddr addr, size_t size) {
    // Check if we are missing some edge case here
    return buffer_ranges.Intersects(addr, size);
}

bool BufferCache::IsRegionCpuModified(VAddr addr, size_t size) {
    return memory_tracker->IsRegionCpuModified(addr, size);
}

bool BufferCache::IsRegionGpuModified(VAddr addr, size_t size) {
    return memory_tracker->IsRegionGpuModified(addr, size);
}

BufferId BufferCache::FindBuffer(VAddr device_addr, u32 size) {
    ASSERT(device_addr != 0);
    const u64 page = device_addr >> CACHING_PAGEBITS;
    const BufferId buffer_id = page_table[page].buffer_id;
    if (!buffer_id) {
        return CreateBuffer(device_addr, size);
    }
    const Buffer& buffer = slot_buffers[buffer_id];
    if (buffer.IsInBounds(device_addr, size)) {
        return buffer_id;
    }
    return CreateBuffer(device_addr, size);
}

BufferCache::OverlapResult BufferCache::ResolveOverlaps(VAddr device_addr, u32 wanted_size) {
    static constexpr int STREAM_LEAP_THRESHOLD = 16;
    boost::container::small_vector<BufferId, 16> overlap_ids;
    VAddr begin = device_addr;
    VAddr end = device_addr + wanted_size;
    int stream_score = 0;
    bool has_stream_leap = false;
    const auto expand_begin = [&](VAddr add_value) {
        static constexpr VAddr min_page = CACHING_PAGESIZE + DEVICE_PAGESIZE;
        if (add_value > begin - min_page) {
            begin = min_page;
            device_addr = DEVICE_PAGESIZE;
            return;
        }
        begin -= add_value;
        device_addr = begin - CACHING_PAGESIZE;
    };
    const auto expand_end = [&](VAddr add_value) {
        static constexpr VAddr max_page = 1ULL << MemoryTracker::MAX_CPU_PAGE_BITS;
        if (add_value > max_page - end) {
            end = max_page;
            return;
        }
        end += add_value;
    };
    if (begin == 0) {
        return OverlapResult{
            .ids = std::move(overlap_ids),
            .begin = begin,
            .end = end,
            .has_stream_leap = has_stream_leap,
        };
    }
    for (; device_addr >> CACHING_PAGEBITS < Common::DivCeil(end, CACHING_PAGESIZE);
         device_addr += CACHING_PAGESIZE) {
        const BufferId overlap_id = page_table[device_addr >> CACHING_PAGEBITS].buffer_id;
        if (!overlap_id) {
            continue;
        }
        Buffer& overlap = slot_buffers[overlap_id];
        if (overlap.is_picked) {
            continue;
        }
        overlap_ids.push_back(overlap_id);
        overlap.is_picked = true;
        const VAddr overlap_device_addr = overlap.CpuAddr();
        const bool expands_left = overlap_device_addr < begin;
        if (expands_left) {
            begin = overlap_device_addr;
        }
        const VAddr overlap_end = overlap_device_addr + overlap.SizeBytes();
        const bool expands_right = overlap_end > end;
        if (overlap_end > end) {
            end = overlap_end;
        }
        stream_score += overlap.StreamScore();
        if (stream_score > STREAM_LEAP_THRESHOLD && !has_stream_leap) {
            // When this memory region has been joined a bunch of times, we assume it's being used
            // as a stream buffer. Increase the size to skip constantly recreating buffers.
            has_stream_leap = true;
            if (expands_right) {
                expand_end(CACHING_PAGESIZE * 128);
            }
            if (expands_left) {
                expand_begin(CACHING_PAGESIZE * 128);
            }
        }
    }
    return OverlapResult{
        .ids = std::move(overlap_ids),
        .begin = begin,
        .end = end,
        .has_stream_leap = has_stream_leap,
    };
}

void BufferCache::JoinOverlap(BufferId new_buffer_id, BufferId overlap_id,
                              bool accumulate_stream_score) {
    Buffer& new_buffer = slot_buffers[new_buffer_id];
    Buffer& overlap = slot_buffers[overlap_id];
    if (accumulate_stream_score) {
        new_buffer.IncreaseStreamScore(overlap.StreamScore() + 1);
    }
    const size_t dst_base_offset = overlap.CpuAddr() - new_buffer.CpuAddr();
    const vk::BufferCopy copy = {
        .srcOffset = 0,
        .dstOffset = dst_base_offset,
        .size = overlap.SizeBytes(),
    };
    scheduler.EndRendering();
    const auto cmdbuf = scheduler.CommandBuffer();

    boost::container::static_vector<vk::BufferMemoryBarrier2, 2> pre_barriers{};
    if (auto src_barrier = overlap.GetBarrier(vk::AccessFlagBits2::eTransferRead,
                                              vk::PipelineStageFlagBits2::eTransfer)) {
        pre_barriers.push_back(*src_barrier);
    }
    if (auto dst_barrier =
            new_buffer.GetBarrier(vk::AccessFlagBits2::eTransferWrite,
                                  vk::PipelineStageFlagBits2::eTransfer, dst_base_offset)) {
        pre_barriers.push_back(*dst_barrier);
    }
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = static_cast<u32>(pre_barriers.size()),
        .pBufferMemoryBarriers = pre_barriers.data(),
    });

    cmdbuf.copyBuffer(overlap.Handle(), new_buffer.Handle(), copy);

    boost::container::static_vector<vk::BufferMemoryBarrier2, 2> post_barriers{};
    if (auto src_barrier =
            overlap.GetBarrier(vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
                               vk::PipelineStageFlagBits2::eAllCommands)) {
        post_barriers.push_back(*src_barrier);
    }
    if (auto dst_barrier = new_buffer.GetBarrier(
            vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
            vk::PipelineStageFlagBits2::eAllCommands, dst_base_offset)) {
        post_barriers.push_back(*dst_barrier);
    }
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = static_cast<u32>(post_barriers.size()),
        .pBufferMemoryBarriers = post_barriers.data(),
    });
    DeleteBuffer(overlap_id);
}

BufferId BufferCache::CreateBuffer(VAddr device_addr, u32 wanted_size) {
    const VAddr device_addr_end = Common::AlignUp(device_addr + wanted_size, CACHING_PAGESIZE);
    device_addr = Common::AlignDown(device_addr, CACHING_PAGESIZE);
    wanted_size = static_cast<u32>(device_addr_end - device_addr);
    const OverlapResult overlap = ResolveOverlaps(device_addr, wanted_size);
    const u32 size = static_cast<u32>(overlap.end - overlap.begin);
    const BufferId new_buffer_id =
        slot_buffers.insert(instance, scheduler, MemoryUsage::DeviceLocal, overlap.begin,
                            AllFlags | vk::BufferUsageFlagBits::eShaderDeviceAddress, size);
    auto& new_buffer = slot_buffers[new_buffer_id];
    for (const BufferId overlap_id : overlap.ids) {
        JoinOverlap(new_buffer_id, overlap_id, !overlap.has_stream_leap);
    }
    Register(new_buffer_id);
    return new_buffer_id;
}

void BufferCache::ProcessFaultBuffer() {
    fault_manager.ProcessFaultBuffer();
}

void BufferCache::Register(BufferId buffer_id) {
    ChangeRegister<true>(buffer_id);
}

void BufferCache::Unregister(BufferId buffer_id) {
    ChangeRegister<false>(buffer_id);
}

template <bool insert>
void BufferCache::ChangeRegister(BufferId buffer_id) {
    Buffer& buffer = slot_buffers[buffer_id];
    const auto size = buffer.SizeBytes();
    const VAddr device_addr_begin = buffer.CpuAddr();
    const VAddr device_addr_end = device_addr_begin + size;
    const u64 page_begin = device_addr_begin / CACHING_PAGESIZE;
    const u64 page_end = Common::DivCeil(device_addr_end, CACHING_PAGESIZE);
    const u64 size_pages = page_end - page_begin;
    for (u64 page = page_begin; page != page_end; ++page) {
        if constexpr (insert) {
            page_table[page].buffer_id = buffer_id;
        } else {
            page_table[page].buffer_id = BufferId{};
        }
    }
    if constexpr (insert) {
        total_used_memory += Common::AlignUp(size, CACHING_PAGESIZE);
        buffer.SetLRUId(lru_cache.Insert(buffer_id, gc_tick));
        boost::container::small_vector<vk::DeviceAddress, 128> bda_addrs;
        bda_addrs.reserve(size_pages);
        for (u64 i = 0; i < size_pages; ++i) {
            vk::DeviceAddress addr = buffer.BufferDeviceAddress() + (i << CACHING_PAGEBITS);
            bda_addrs.push_back(addr);
        }
        WriteDataBuffer(bda_pagetable_buffer, page_begin * sizeof(vk::DeviceAddress),
                        bda_addrs.data(), bda_addrs.size() * sizeof(vk::DeviceAddress));
        buffer_ranges.Add(buffer.CpuAddr(), buffer.SizeBytes(), buffer_id);
    } else {
        total_used_memory -= Common::AlignUp(size, CACHING_PAGESIZE);
        lru_cache.Free(buffer.LRUId());
        const u64 offset = bda_pagetable_buffer.Offset(page_begin * sizeof(vk::DeviceAddress));
        bda_pagetable_buffer.Fill(offset, size_pages * sizeof(vk::DeviceAddress), 0);
        buffer_ranges.Subtract(buffer.CpuAddr(), buffer.SizeBytes());
    }
}

bool BufferCache::SynchronizeBuffer(Buffer& buffer, VAddr device_addr, u32 size, bool is_written,
                                    bool is_texel_buffer) {
    boost::container::small_vector<vk::BufferCopy, 4> copies;
    size_t total_size_bytes = 0;
    VAddr buffer_start = buffer.CpuAddr();
    vk::Buffer src_buffer = VK_NULL_HANDLE;
    memory_tracker->ForEachUploadRange(
        device_addr, size, is_written,
        [&](u64 device_addr_out, u64 range_size) {
            copies.emplace_back(total_size_bytes, device_addr_out - buffer_start, range_size);
            total_size_bytes += range_size;
        },
        [&] { src_buffer = UploadCopies(buffer, copies, total_size_bytes); });

    if (src_buffer) {
        scheduler.EndRendering();
        const auto cmdbuf = scheduler.CommandBuffer();
        const vk::BufferMemoryBarrier2 pre_barrier = {
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite |
                             vk::AccessFlagBits2::eTransferRead |
                             vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eTransfer,
            .dstAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .buffer = buffer.Handle(),
            .offset = 0,
            .size = buffer.SizeBytes(),
        };
        const vk::BufferMemoryBarrier2 post_barrier = {
            .srcStageMask = vk::PipelineStageFlagBits2::eTransfer,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .dstAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
            .buffer = buffer.Handle(),
            .offset = 0,
            .size = buffer.SizeBytes(),
        };
        cmdbuf.pipelineBarrier2(vk::DependencyInfo{
            .dependencyFlags = vk::DependencyFlagBits::eByRegion,
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &pre_barrier,
        });
        cmdbuf.copyBuffer(src_buffer, buffer.buffer, copies);
        cmdbuf.pipelineBarrier2(vk::DependencyInfo{
            .dependencyFlags = vk::DependencyFlagBits::eByRegion,
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &post_barrier,
        });
        TouchBuffer(buffer);
    }
    if (is_texel_buffer && !is_written) {
        return SynchronizeBufferFromImage(buffer, device_addr, size);
    }
    return false;
}

vk::Buffer BufferCache::UploadCopies(Buffer& buffer, std::span<vk::BufferCopy> copies,
                                     size_t total_size_bytes) {
    if (copies.empty()) {
        return VK_NULL_HANDLE;
    }
    const auto [staging, offset] = staging_buffer.Map(total_size_bytes);
    if (staging) {
        for (auto& copy : copies) {
            u8* const src_pointer = staging + copy.srcOffset;
            const VAddr device_addr = buffer.CpuAddr() + copy.dstOffset;
            memory->CopySparseMemory(device_addr, src_pointer, copy.size);
            // Apply the staging offset
            copy.srcOffset += offset;
        }
        staging_buffer.Commit();
        return staging_buffer.Handle();
    } else {
        // For large one time transfers use a temporary host buffer.
        auto temp_buffer =
            std::make_unique<Buffer>(instance, scheduler, MemoryUsage::Upload, 0,
                                     vk::BufferUsageFlagBits::eTransferSrc, total_size_bytes);
        const vk::Buffer src_buffer = temp_buffer->Handle();
        u8* const staging = temp_buffer->mapped_data.data();
        for (const auto& copy : copies) {
            u8* const src_pointer = staging + copy.srcOffset;
            const VAddr device_addr = buffer.CpuAddr() + copy.dstOffset;
            memory->CopySparseMemory(device_addr, src_pointer, copy.size);
        }
        scheduler.DeferOperation([buffer = std::move(temp_buffer)]() mutable { buffer.reset(); });
        return src_buffer;
    }
}

bool BufferCache::SynchronizeBufferFromImage(Buffer& buffer, VAddr device_addr, u32 size) {
    if (auto type = texture_cache.IsMeta(device_addr)) {
        ASSERT(*type == TextureCache::MetaType::HTile);
        static constexpr u32 ZmaskUncompressed = 0xf;
        buffer.Fill(buffer.Offset(device_addr), size, ZmaskUncompressed);
        return true;
    }
    const ImageId image_id = texture_cache.FindImageFromRange(device_addr, size);
    if (!image_id) {
        return false;
    }
    Image& image = texture_cache.GetImage(image_id);
    ASSERT_MSG(device_addr == image.info.guest_address,
               "Texel buffer aliases image subresources {:x} : {:x}", device_addr,
               image.info.guest_address);
    const u32 buf_offset = buffer.Offset(image.info.guest_address);
    boost::container::small_vector<vk::BufferImageCopy, 8> buffer_copies;
    u32 copy_size = 0;
    for (u32 mip = 0; mip < image.info.resources.levels; mip++) {
        const auto& mip_info = image.info.mips_layout[mip];
        const u32 width = std::max(image.info.size.width >> mip, 1u);
        const u32 height = std::max(image.info.size.height >> mip, 1u);
        const u32 depth = std::max(image.info.size.depth >> mip, 1u);
        if (buf_offset + mip_info.offset + mip_info.size > buffer.SizeBytes()) {
            break;
        }
        buffer_copies.push_back(vk::BufferImageCopy{
            .bufferOffset = mip_info.offset,
            .bufferRowLength = mip_info.pitch,
            .bufferImageHeight = mip_info.height,
            .imageSubresource{
                .aspectMask = image.aspect_mask & ~vk::ImageAspectFlagBits::eStencil,
                .mipLevel = mip,
                .baseArrayLayer = 0,
                .layerCount = image.info.resources.layers,
            },
            .imageOffset = {0, 0, 0},
            .imageExtent = {width, height, depth},
        });
        copy_size += mip_info.size;
    }
    if (copy_size == 0) {
        return false;
    }
    auto& tile_manager = texture_cache.GetTileManager();
    tile_manager.TileImage(image, buffer_copies, buffer.Handle(), buf_offset, copy_size);
    return true;
}

void BufferCache::SynchronizeBuffersInRange(VAddr device_addr, u64 size) {
    const VAddr device_addr_end = device_addr + size;
    ForEachBufferInRange(device_addr, size, [&](BufferId buffer_id, Buffer& buffer) {
        RENDERER_TRACE;
        VAddr start = std::max(buffer.CpuAddr(), device_addr);
        VAddr end = std::min(buffer.CpuAddr() + buffer.SizeBytes(), device_addr_end);
        u32 size = static_cast<u32>(end - start);
        SynchronizeBuffer(buffer, start, size, false, false);
    });
}

void BufferCache::WriteDataBuffer(Buffer& buffer, VAddr address, const void* value, u32 num_bytes) {
    vk::BufferCopy copy = {
        .srcOffset = 0,
        .dstOffset = buffer.Offset(address),
        .size = num_bytes,
    };
    vk::Buffer src_buffer = staging_buffer.Handle();
    if (num_bytes < StagingBufferSize) {
        const auto [staging, offset] = staging_buffer.Map(num_bytes);
        std::memcpy(staging, value, num_bytes);
        copy.srcOffset = offset;
        staging_buffer.Commit();
    } else {
        // For large one time transfers use a temporary host buffer.
        // RenderDoc can lag quite a bit if the stream buffer is too large.
        Buffer temp_buffer{
            instance, scheduler, MemoryUsage::Upload, 0, vk::BufferUsageFlagBits::eTransferSrc,
            num_bytes};
        src_buffer = temp_buffer.Handle();
        u8* const staging = temp_buffer.mapped_data.data();
        std::memcpy(staging, value, num_bytes);
        scheduler.DeferOperation([buffer = std::move(temp_buffer)]() mutable {});
    }
    scheduler.EndRendering();
    const auto cmdbuf = scheduler.CommandBuffer();
    const vk::BufferMemoryBarrier2 pre_barrier = {
        .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryRead,
        .dstStageMask = vk::PipelineStageFlagBits2::eTransfer,
        .dstAccessMask = vk::AccessFlagBits2::eTransferWrite,
        .buffer = buffer.Handle(),
        .offset = buffer.Offset(address),
        .size = num_bytes,
    };
    const vk::BufferMemoryBarrier2 post_barrier = {
        .srcStageMask = vk::PipelineStageFlagBits2::eTransfer,
        .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .dstAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
        .buffer = buffer.Handle(),
        .offset = buffer.Offset(address),
        .size = num_bytes,
    };
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = 1,
        .pBufferMemoryBarriers = &pre_barrier,
    });
    cmdbuf.copyBuffer(src_buffer, buffer.Handle(), copy);
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = 1,
        .pBufferMemoryBarriers = &post_barrier,
    });
}

void BufferCache::RunGarbageCollector() {
    SCOPE_EXIT {
        ++gc_tick;
    };
    if (instance.CanReportMemoryUsage()) {
        total_used_memory = instance.GetDeviceMemoryUsage();
    }
    if (total_used_memory < trigger_gc_memory) {
        return;
    }
    const bool aggressive = total_used_memory >= critical_gc_memory;
    const u64 ticks_to_destroy = std::min<u64>(aggressive ? 80 : 160, gc_tick);
    int max_deletions = aggressive ? 64 : 32;
    int deleted_count = 0;
    int skipped_dirty_count = 0;
    u64 deleted_bytes = 0;
    bool downloaded_dirty_buffer = false;
    const auto clean_up = [&](BufferId buffer_id) {
        if (max_deletions == 0) {
            return true;
        }
        Buffer& buffer = slot_buffers[buffer_id];

        if (IsRegionGpuModified(buffer.CpuAddr(), buffer.SizeBytes())) {
            // Keep normal-pressure collection stall-free. Under critical pressure, synchronously
            // preserve at most one bounded dirty buffer per pass before eviction. Reserving 1 MiB
            // below the ring capacity covers 64-byte packing alignment for fragmented 4 KiB
            // tracker ranges.
            constexpr u64 MaxGcDownloadSize = DownloadBufferSize - 1_MB;
            if (!aggressive || downloaded_dirty_buffer || buffer.SizeBytes() > MaxGcDownloadSize) {
                ++skipped_dirty_count;
                return false;
            }
            DownloadBufferMemory(buffer, buffer.CpuAddr(), buffer.SizeBytes());
            downloaded_dirty_buffer = true;
        }

        --max_deletions;
        ++deleted_count;
        deleted_bytes += buffer.SizeBytes();
        memory_tracker->MarkRegionAsCpuModified(buffer.CpuAddr(), buffer.SizeBytes());
        DeleteBuffer(buffer_id);
        return false;
    };
    lru_cache.ForEachItemBelow(gc_tick - ticks_to_destroy, clean_up);
    if (deleted_count > 0 && (downloaded_dirty_buffer || gc_tick % 60 == 0)) {
        LOG_INFO(Render_Vulkan,
                 "Buffer GC: deleted {} buffers ({} bytes), skipped {} dirty buffers, usage {} / "
                 "trigger {} / critical {} bytes",
                 deleted_count, deleted_bytes, skipped_dirty_count, total_used_memory,
                 trigger_gc_memory, critical_gc_memory);
    }
}

void BufferCache::TouchBuffer(const Buffer& buffer) {
    lru_cache.Touch(buffer.LRUId(), gc_tick);
}

void BufferCache::DeleteBuffer(BufferId buffer_id) {
    Buffer& buffer = slot_buffers[buffer_id];
    Unregister(buffer_id);
    scheduler.DeferOperation([this, buffer_id] { slot_buffers.erase(buffer_id); });
    buffer.is_deleted = true;
}

} // namespace VideoCore
