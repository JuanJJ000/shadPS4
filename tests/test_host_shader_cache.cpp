// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "video_core/renderer_vulkan/host_shader_cache.h"

namespace Vulkan {
namespace {

TEST(HostShaderCache, KeyCoversSourceStageAndOrderedDefines) {
    const std::vector<std::string> defines{"BITS_PER_PIXEL=32", "IS_TILER=1"};
    const auto key = HostShaderCacheKey("shader source", vk::ShaderStageFlagBits::eCompute,
                                        defines);

    EXPECT_EQ(key, HostShaderCacheKey("shader source", vk::ShaderStageFlagBits::eCompute,
                                      defines));
    EXPECT_NE(key, HostShaderCacheKey("changed source", vk::ShaderStageFlagBits::eCompute,
                                      defines));
    EXPECT_NE(key,
              HostShaderCacheKey("shader source", vk::ShaderStageFlagBits::eFragment, defines));

    const std::vector<std::string> reordered{"IS_TILER=1", "BITS_PER_PIXEL=32"};
    EXPECT_NE(key, HostShaderCacheKey("shader source", vk::ShaderStageFlagBits::eCompute,
                                      reordered));

    const std::vector<std::string> changed{"BITS_PER_PIXEL=64", "IS_TILER=1"};
    EXPECT_NE(key,
              HostShaderCacheKey("shader source", vk::ShaderStageFlagBits::eCompute, changed));
}

TEST(HostShaderCache, ValidatesRequiredSpirvHeaderFields) {
    const std::vector<u32> valid{SpirvMagicNumber, HostShaderSpirvVersion, 1, 8, 0};
    EXPECT_TRUE(IsValidHostShaderSpirv(valid));

    auto invalid = valid;
    invalid[0] = 0;
    EXPECT_FALSE(IsValidHostShaderSpirv(invalid));
    invalid = valid;
    invalid[1] = 0x00010600;
    EXPECT_FALSE(IsValidHostShaderSpirv(invalid));
    invalid = valid;
    invalid[3] = 0;
    EXPECT_FALSE(IsValidHostShaderSpirv(invalid));
    invalid = valid;
    invalid[4] = 1;
    EXPECT_FALSE(IsValidHostShaderSpirv(invalid));
    EXPECT_FALSE(IsValidHostShaderSpirv(std::span<const u32>{valid}.first(4)));
}

} // namespace
} // namespace Vulkan
