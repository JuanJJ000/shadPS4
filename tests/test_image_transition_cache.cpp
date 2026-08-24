// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <gtest/gtest.h>

#include "video_core/texture_cache/image.h"

namespace VideoCore {
namespace {

constexpr SubresourceRange CachedRange{
    .base = {.level = 1, .layer = 3},
    .extent = {.levels = 4, .layers = 64},
};

TEST(ImageTransitionCache, RequiresExactRangeStateAndGeneration) {
    constexpr u64 generation = 7;
    const Image::ReadTransitionCache cache{
        .range = CachedRange,
        .layout = vk::ImageLayout::eShaderReadOnlyOptimal,
        .access_mask = vk::AccessFlagBits2::eShaderRead,
        .generation = generation,
    };

    EXPECT_TRUE(cache.Matches(CachedRange, vk::ImageLayout::eShaderReadOnlyOptimal,
                              vk::AccessFlagBits2::eShaderRead, generation));

    auto different_range = CachedRange;
    ++different_range.base.layer;
    EXPECT_FALSE(cache.Matches(different_range, vk::ImageLayout::eShaderReadOnlyOptimal,
                               vk::AccessFlagBits2::eShaderRead, generation));
    EXPECT_FALSE(cache.Matches(CachedRange, vk::ImageLayout::eGeneral,
                               vk::AccessFlagBits2::eShaderRead, generation));
    EXPECT_FALSE(cache.Matches(CachedRange, vk::ImageLayout::eShaderReadOnlyOptimal,
                               vk::AccessFlagBits2::eTransferRead, generation));
    EXPECT_FALSE(cache.Matches(CachedRange, vk::ImageLayout::eShaderReadOnlyOptimal,
                               vk::AccessFlagBits2::eShaderRead, generation + 1));
}

TEST(ImageTransitionCache, IdentifiesWritesThatMustNotUseTheFastPath) {
    EXPECT_FALSE(Image::IsWriteAccess(vk::AccessFlagBits2::eShaderRead));
    EXPECT_FALSE(Image::IsWriteAccess(vk::AccessFlagBits2::eTransferRead));
    EXPECT_TRUE(Image::IsWriteAccess(vk::AccessFlagBits2::eTransferWrite));
    EXPECT_TRUE(Image::IsWriteAccess(vk::AccessFlagBits2::eShaderWrite));
    EXPECT_TRUE(Image::IsWriteAccess(vk::AccessFlagBits2::eMemoryWrite));
}

} // namespace
} // namespace VideoCore
