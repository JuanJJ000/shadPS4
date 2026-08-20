// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <string_view>
#include <vector>

#include <vulkan/vulkan.h>

namespace {

constexpr VkImageCreateFlags BaseFlags =
    VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT | VK_IMAGE_CREATE_EXTENDED_USAGE_BIT;
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

void Probe(VkPhysicalDevice gpu, VkFormat format, std::string_view format_name, VkImageType type,
           std::string_view type_name, bool block_view, bool storage) {
    VkPhysicalDeviceImageFormatInfo2 info{
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
        .format = format,
        .type = type,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = BaseUsage | (storage ? VK_IMAGE_USAGE_STORAGE_BIT : 0),
        .flags = BaseFlags |
                 (block_view ? VK_IMAGE_CREATE_BLOCK_TEXEL_VIEW_COMPATIBLE_BIT : 0),
    };
    VkImageFormatProperties2 properties{
        .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
    };
    const VkResult result = vkGetPhysicalDeviceImageFormatProperties2(gpu, &info, &properties);
    std::cout << std::left << std::setw(12) << format_name << std::setw(5) << type_name
              << " block-view=" << (block_view ? "yes" : "no ")
              << " storage=" << (storage ? "yes" : "no ") << " -> " << ResultName(result)
              << " (" << result << ")\n";
}

} // namespace

int main() {
    const VkApplicationInfo app_info{
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "shadPS4 Deck image probe",
        .apiVersion = VK_API_VERSION_1_3,
    };
    const VkInstanceCreateInfo instance_info{
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app_info,
    };
    VkInstance instance{};
    if (const VkResult result = vkCreateInstance(&instance_info, nullptr, &instance);
        result != VK_SUCCESS) {
        std::cerr << "vkCreateInstance failed: " << result << '\n';
        return 1;
    }

    std::uint32_t gpu_count{};
    vkEnumeratePhysicalDevices(instance, &gpu_count, nullptr);
    std::vector<VkPhysicalDevice> gpus(gpu_count);
    vkEnumeratePhysicalDevices(instance, &gpu_count, gpus.data());
    if (gpus.empty()) {
        std::cerr << "No Vulkan device found\n";
        vkDestroyInstance(instance, nullptr);
        return 1;
    }

    for (const VkPhysicalDevice gpu : gpus) {
        VkPhysicalDeviceProperties properties{};
        vkGetPhysicalDeviceProperties(gpu, &properties);
        std::cout << "\nGPU: " << properties.deviceName << "\n\n";

        for (const auto [format, name] :
             std::array{std::pair{VK_FORMAT_BC1_RGBA_UNORM_BLOCK, std::string_view{"BC1_UNORM"}},
                        std::pair{VK_FORMAT_BC5_UNORM_BLOCK, std::string_view{"BC5_UNORM"}}}) {
            for (const auto [type, type_name] :
                 std::array{std::pair{VK_IMAGE_TYPE_1D, std::string_view{"1D"}},
                            std::pair{VK_IMAGE_TYPE_2D, std::string_view{"2D"}}}) {
                Probe(gpu, format, name, type, type_name, true, true);
                Probe(gpu, format, name, type, type_name, true, false);
                Probe(gpu, format, name, type, type_name, false, false);
            }
        }

        VkPhysicalDeviceMemoryBudgetPropertiesEXT budget{
            .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT,
        };
        VkPhysicalDeviceMemoryProperties2 memory{
            .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2,
            .pNext = &budget,
        };
        vkGetPhysicalDeviceMemoryProperties2(gpu, &memory);
        std::cout << "\nMemory heaps:\n";
        for (std::uint32_t index = 0; index < memory.memoryProperties.memoryHeapCount; ++index) {
            constexpr double GiB = 1024.0 * 1024.0 * 1024.0;
            std::cout << "  heap " << index << ": size="
                      << memory.memoryProperties.memoryHeaps[index].size / GiB
                      << " GiB, budget=" << budget.heapBudget[index] / GiB
                      << " GiB, usage=" << budget.heapUsage[index] / GiB << " GiB\n";
        }
    }

    vkDestroyInstance(instance, nullptr);
    return 0;
}
