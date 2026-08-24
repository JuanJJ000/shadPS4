// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <span>
#include <string>
#include <string_view>

#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan {

constexpr u32 SpirvMagicNumber = 0x07230203;
constexpr u32 HostShaderSpirvVersion = 0x00010300;

[[nodiscard]] inline bool IsValidHostShaderSpirv(std::span<const u32> code) {
    return code.size() >= 5 && code[0] == SpirvMagicNumber &&
           code[1] == HostShaderSpirvVersion && code[3] != 0 && code[4] == 0;
}

[[nodiscard]] inline u64 HostShaderCacheKey(std::string_view source,
                                            vk::ShaderStageFlagBits stage,
                                            std::span<const std::string> defines) {
    constexpr u64 FnvOffset = 14695981039346656037ULL;
    constexpr u64 FnvPrime = 1099511628211ULL;
    u64 hash = FnvOffset;

    const auto hash_byte = [&](u8 value) {
        hash ^= value;
        hash *= FnvPrime;
    };
    const auto hash_u64 = [&](u64 value) {
        for (u32 i = 0; i < sizeof(value); ++i) {
            hash_byte(static_cast<u8>(value >> (i * 8)));
        }
    };
    const auto hash_string = [&](std::string_view value) {
        hash_u64(value.size());
        for (const char ch : value) {
            hash_byte(static_cast<u8>(ch));
        }
    };

    hash_string("shadps4-host-spv-v1");
    hash_u64(static_cast<u32>(stage));
    hash_string(source);
    hash_u64(defines.size());
    for (const auto& define : defines) {
        hash_string(define);
    }
    return hash;
}

} // namespace Vulkan
