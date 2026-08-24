// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <string_view>
#include <vector>

#include <vulkan/vulkan.h>

namespace {

constexpr VkImageUsageFlags BaseUsage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
                                        VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                                        VK_IMAGE_USAGE_SAMPLED_BIT;

const char* ResultName(VkResult result) {
    switch (result) {
    case VK_SUCCESS:
        return "supported";
    case VK_ERROR_FORMAT_NOT_SUPPORTED:
        return "format-not-supported";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY:
        return "out-of-device-memory";
    default:
        return "other-error";
    }
}

bool Probe(VkPhysicalDevice gpu, VkFormat format, std::string_view format_name, VkImageType type,
           std::string_view type_name, bool mutable_format, bool extended_usage, bool block_view,
           bool storage) {
    VkImageCreateFlags flags{};
    if (mutable_format) {
        flags |= VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT;
    }
    if (extended_usage) {
        flags |= VK_IMAGE_CREATE_EXTENDED_USAGE_BIT;
    }
    if (block_view) {
        flags |= VK_IMAGE_CREATE_BLOCK_TEXEL_VIEW_COMPATIBLE_BIT;
    }
    VkPhysicalDeviceImageFormatInfo2 info{
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
        .pNext = nullptr,
        .format = format,
        .type = type,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = BaseUsage | (storage ? VK_IMAGE_USAGE_STORAGE_BIT : 0),
        .flags = flags,
    };
    VkImageFormatProperties2 properties{
        .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
        .pNext = nullptr,
        .imageFormatProperties = {},
    };
    const VkResult result = vkGetPhysicalDeviceImageFormatProperties2(gpu, &info, &properties);
    std::cout << std::left << std::setw(12) << format_name << std::setw(4) << type_name
              << " mutable=" << (mutable_format ? "yes" : "no ")
              << " extended=" << (extended_usage ? "yes" : "no ")
              << " block-view=" << (block_view ? "yes" : "no ")
              << " storage=" << (storage ? "yes" : "no ") << " -> " << ResultName(result)
              << " (" << result << ')';
    if (result == VK_SUCCESS) {
        const auto& limits = properties.imageFormatProperties;
        std::cout << " max=" << limits.maxExtent.width << 'x' << limits.maxExtent.height << 'x'
                  << limits.maxExtent.depth << " mips=" << limits.maxMipLevels
                  << " layers=" << limits.maxArrayLayers << " samples=0x" << std::hex
                  << limits.sampleCounts << std::dec << " bytes=" << limits.maxResourceSize;
    }
    std::cout << '\n';
    return result == VK_SUCCESS || result == VK_ERROR_FORMAT_NOT_SUPPORTED;
}

} // namespace

int main() {
    const VkApplicationInfo app_info{
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pNext = nullptr,
        .pApplicationName = "shadPS4 Deck image probe",
        .applicationVersion = 0,
        .pEngineName = nullptr,
        .engineVersion = 0,
        .apiVersion = VK_API_VERSION_1_3,
    };
    const VkInstanceCreateInfo instance_info{
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pNext = nullptr,
        .flags = 0,
        .pApplicationInfo = &app_info,
        .enabledLayerCount = 0,
        .ppEnabledLayerNames = nullptr,
        .enabledExtensionCount = 0,
        .ppEnabledExtensionNames = nullptr,
    };
    VkInstance instance{};
    if (const VkResult result = vkCreateInstance(&instance_info, nullptr, &instance);
        result != VK_SUCCESS) {
        std::cerr << "vkCreateInstance failed: " << result << '\n';
        return 1;
    }

    std::uint32_t gpu_count{};
    if (const VkResult result = vkEnumeratePhysicalDevices(instance, &gpu_count, nullptr);
        result != VK_SUCCESS) {
        std::cerr << "vkEnumeratePhysicalDevices failed: " << result << '\n';
        vkDestroyInstance(instance, nullptr);
        return 1;
    }
    std::vector<VkPhysicalDevice> gpus(gpu_count);
    if (const VkResult result =
            vkEnumeratePhysicalDevices(instance, &gpu_count, gpus.data());
        result != VK_SUCCESS && result != VK_INCOMPLETE) {
        std::cerr << "vkEnumeratePhysicalDevices failed: " << result << '\n';
        vkDestroyInstance(instance, nullptr);
        return 1;
    }
    if (gpus.empty()) {
        std::cerr << "No Vulkan device found\n";
        vkDestroyInstance(instance, nullptr);
        return 1;
    }

    bool query_failed = false;
    for (const VkPhysicalDevice gpu : gpus) {
        VkPhysicalDeviceProperties properties{};
        vkGetPhysicalDeviceProperties(gpu, &properties);
        std::cout << "\nGPU: " << properties.deviceName << "\n\n";

        for (const auto [format, name] :
             std::array{std::pair{VK_FORMAT_BC1_RGBA_UNORM_BLOCK, std::string_view{"BC1_UNORM"}},
                        std::pair{VK_FORMAT_BC1_RGBA_SRGB_BLOCK, std::string_view{"BC1_SRGB"}},
                        std::pair{VK_FORMAT_BC2_UNORM_BLOCK, std::string_view{"BC2_UNORM"}},
                        std::pair{VK_FORMAT_BC2_SRGB_BLOCK, std::string_view{"BC2_SRGB"}},
                        std::pair{VK_FORMAT_BC3_UNORM_BLOCK, std::string_view{"BC3_UNORM"}},
                        std::pair{VK_FORMAT_BC3_SRGB_BLOCK, std::string_view{"BC3_SRGB"}},
                        std::pair{VK_FORMAT_BC4_UNORM_BLOCK, std::string_view{"BC4_UNORM"}},
                        std::pair{VK_FORMAT_BC4_SNORM_BLOCK, std::string_view{"BC4_SNORM"}},
                        std::pair{VK_FORMAT_BC5_UNORM_BLOCK, std::string_view{"BC5_UNORM"}},
                        std::pair{VK_FORMAT_BC5_SNORM_BLOCK, std::string_view{"BC5_SNORM"}},
                        std::pair{VK_FORMAT_BC6H_UFLOAT_BLOCK, std::string_view{"BC6_UFLOAT"}},
                        std::pair{VK_FORMAT_BC6H_SFLOAT_BLOCK, std::string_view{"BC6_SFLOAT"}},
                        std::pair{VK_FORMAT_BC7_UNORM_BLOCK, std::string_view{"BC7_UNORM"}},
                        std::pair{VK_FORMAT_BC7_SRGB_BLOCK, std::string_view{"BC7_SRGB"}}}) {
            for (const auto [type, type_name] :
                 std::array{std::pair{VK_IMAGE_TYPE_1D, std::string_view{"1D"}},
                            std::pair{VK_IMAGE_TYPE_2D, std::string_view{"2D"}}}) {
                for (const bool mutable_format : {false, true}) {
                    for (const bool extended_usage : {false, true}) {
                        for (const bool block_view : {false, true}) {
                            for (const bool storage : {false, true}) {
                                query_failed |=
                                    !Probe(gpu, format, name, type, type_name, mutable_format,
                                           extended_usage, block_view, storage);
                            }
                        }
                    }
                }
            }
        }

        VkPhysicalDeviceMemoryProperties memory{};
        vkGetPhysicalDeviceMemoryProperties(gpu, &memory);
        std::cout << "\nMemory heaps:\n";
        for (std::uint32_t index = 0; index < memory.memoryHeapCount; ++index) {
            constexpr double GiB = 1024.0 * 1024.0 * 1024.0;
            std::cout << "  heap " << index << ": size=" << memory.memoryHeaps[index].size / GiB
                      << " GiB, device-local="
                      << (memory.memoryHeaps[index].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT ? "yes"
                                                                                           : "no")
                      << '\n';
        }
    }

    vkDestroyInstance(instance, nullptr);
    return query_failed ? 1 : 0;
}
