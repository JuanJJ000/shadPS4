// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

#include <vulkan/vulkan.h>

namespace {

constexpr VkDeviceSize TestSize = 512 * 1024;
constexpr std::uint32_t Iterations = 100;
constexpr std::uint32_t BenchmarkRounds = 100;
constexpr VkBufferUsageFlags BufferUsage =
    VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT |
    VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT | VK_BUFFER_USAGE_INDEX_BUFFER_BIT |
    VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT |
    VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
constexpr VkExternalMemoryHandleTypeFlagBits HostHandle =
    VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT;

VkDeviceSize AlignUp(VkDeviceSize value, VkDeviceSize alignment) {
  return (value + alignment - 1) & ~(alignment - 1);
}

std::uint32_t FindMemoryType(const VkPhysicalDeviceMemoryProperties &memory,
                             std::uint32_t allowed_types,
                             VkMemoryPropertyFlags required,
                             VkMemoryPropertyFlags preferred) {
  std::uint32_t fallback = std::numeric_limits<std::uint32_t>::max();
  for (std::uint32_t index = 0; index < memory.memoryTypeCount; ++index) {
    if ((allowed_types & (1U << index)) == 0) {
      continue;
    }
    const auto flags = memory.memoryTypes[index].propertyFlags;
    if ((flags & required) != required) {
      continue;
    }
    if ((flags & preferred) == preferred) {
      return index;
    }
    fallback = index;
  }
  return fallback;
}

bool HasDeviceExtension(VkPhysicalDevice gpu, std::string_view wanted) {
  std::uint32_t count{};
  if (vkEnumerateDeviceExtensionProperties(gpu, nullptr, &count, nullptr) !=
      VK_SUCCESS) {
    return false;
  }
  std::vector<VkExtensionProperties> extensions(count);
  if (vkEnumerateDeviceExtensionProperties(gpu, nullptr, &count,
                                           extensions.data()) != VK_SUCCESS) {
    return false;
  }
  return std::ranges::any_of(extensions, [wanted](const auto &extension) {
    return wanted == extension.extensionName;
  });
}

const char *PassFail(bool passed) { return passed ? "pass" : "fail"; }

struct BenchmarkSample {
  double wall_microseconds{};
  double gpu_fill_microseconds{};
};

struct Summary {
  double median{};
  double p95{};
};

Summary Summarize(std::vector<double> values) {
  std::ranges::sort(values);
  const auto midpoint = values.size() / 2;
  const double median = values.size() % 2 == 0
                            ? (values[midpoint - 1] + values[midpoint]) / 2.0
                            : values[midpoint];
  const auto p95_rank = static_cast<std::size_t>(
      std::ceil(0.95 * static_cast<double>(values.size())) - 1.0);
  return {.median = median, .p95 = values[p95_rank]};
}

Summary Summarize(const std::vector<BenchmarkSample> &samples,
                  double BenchmarkSample::*member) {
  std::vector<double> values;
  values.reserve(samples.size());
  for (const auto &sample : samples) {
    values.push_back(sample.*member);
  }
  return Summarize(std::move(values));
}

} // namespace

int main() {
  const VkApplicationInfo app_info{
      .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
      .pApplicationName = "shadPS4 external host buffer probe",
      .apiVersion = VK_API_VERSION_1_3,
  };
  const VkInstanceCreateInfo instance_info{
      .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
      .pApplicationInfo = &app_info,
  };
  VkInstance instance{};
  VkResult result = vkCreateInstance(&instance_info, nullptr, &instance);
  if (result != VK_SUCCESS) {
    std::cerr << "result=fail stage=create_instance vk_result=" << result
              << '\n';
    return 1;
  }

  std::uint32_t gpu_count{};
  vkEnumeratePhysicalDevices(instance, &gpu_count, nullptr);
  std::vector<VkPhysicalDevice> gpus(gpu_count);
  vkEnumeratePhysicalDevices(instance, &gpu_count, gpus.data());
  if (gpus.empty()) {
    std::cerr << "result=fail stage=enumerate_gpu reason=no_device\n";
    vkDestroyInstance(instance, nullptr);
    return 1;
  }
  const VkPhysicalDevice gpu = gpus.front();
  VkPhysicalDeviceProperties gpu_properties{};
  VkPhysicalDeviceMemoryProperties memory_properties{};
  vkGetPhysicalDeviceProperties(gpu, &gpu_properties);
  vkGetPhysicalDeviceMemoryProperties(gpu, &memory_properties);
  std::cout << "gpu=" << gpu_properties.deviceName << '\n';

  const bool extension_available =
      HasDeviceExtension(gpu, VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME);
  std::cout << "extension=" << VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME
            << " available=" << extension_available << '\n';
  if (!extension_available) {
    std::cerr << "result=fail stage=device_extension reason=unsupported\n";
    vkDestroyInstance(instance, nullptr);
    return 1;
  }

  VkPhysicalDeviceExternalMemoryHostPropertiesEXT host_properties{
      .sType =
          VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT,
  };
  VkPhysicalDeviceProperties2 properties2{
      .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
      .pNext = &host_properties,
  };
  vkGetPhysicalDeviceProperties2(gpu, &properties2);
  std::cout << "min_imported_host_pointer_alignment="
            << host_properties.minImportedHostPointerAlignment << '\n';

  const VkPhysicalDeviceExternalBufferInfo external_buffer_info{
      .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_BUFFER_INFO,
      .flags = 0,
      .usage = BufferUsage,
      .handleType = HostHandle,
  };
  VkExternalBufferProperties external_buffer_properties{
      .sType = VK_STRUCTURE_TYPE_EXTERNAL_BUFFER_PROPERTIES,
  };
  vkGetPhysicalDeviceExternalBufferProperties(gpu, &external_buffer_info,
                                              &external_buffer_properties);
  const auto external_features =
      external_buffer_properties.externalMemoryProperties
          .externalMemoryFeatures;
  const bool importable =
      (external_features & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT) != 0;
  std::cout << "buffer_usage=0x" << std::hex << BufferUsage
            << " external_features=0x" << external_features
            << " compatible_handles=0x"
            << external_buffer_properties.externalMemoryProperties
                   .compatibleHandleTypes
            << std::dec << " importable=" << importable << '\n';
  if (!importable) {
    std::cerr << "result=fail stage=external_buffer_properties "
                 "reason=not_importable\n";
    vkDestroyInstance(instance, nullptr);
    return 1;
  }

  std::uint32_t queue_family_count{};
  vkGetPhysicalDeviceQueueFamilyProperties(gpu, &queue_family_count, nullptr);
  std::vector<VkQueueFamilyProperties> queue_families(queue_family_count);
  vkGetPhysicalDeviceQueueFamilyProperties(gpu, &queue_family_count,
                                           queue_families.data());
  auto queue_family =
      std::ranges::find_if(queue_families, [](const auto &family) {
        return family.queueCount != 0 &&
               (family.queueFlags & VK_QUEUE_TRANSFER_BIT) != 0;
      });
  if (queue_family == queue_families.end()) {
    std::cerr << "result=fail stage=queue_family reason=no_transfer_queue\n";
    vkDestroyInstance(instance, nullptr);
    return 1;
  }
  const std::uint32_t queue_family_index = static_cast<std::uint32_t>(
      std::distance(queue_families.begin(), queue_family));
  constexpr float queue_priority = 1.0F;
  const VkDeviceQueueCreateInfo queue_info{
      .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
      .queueFamilyIndex = queue_family_index,
      .queueCount = 1,
      .pQueuePriorities = &queue_priority,
  };
  constexpr const char *device_extensions[] = {
      VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME};
  const VkDeviceCreateInfo device_info{
      .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
      .queueCreateInfoCount = 1,
      .pQueueCreateInfos = &queue_info,
      .enabledExtensionCount = 1,
      .ppEnabledExtensionNames = device_extensions,
  };
  VkDevice device{};
  result = vkCreateDevice(gpu, &device_info, nullptr, &device);
  if (result != VK_SUCCESS) {
    std::cerr << "result=fail stage=create_device vk_result=" << result << '\n';
    vkDestroyInstance(instance, nullptr);
    return 1;
  }

  VkExternalMemoryBufferCreateInfo external_create_info{
      .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO,
      .handleTypes = HostHandle,
  };
  const VkBufferCreateInfo imported_buffer_info{
      .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
      .pNext = &external_create_info,
      .size = TestSize,
      .usage = BufferUsage,
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
  };
  VkBuffer imported_buffer{};
  result =
      vkCreateBuffer(device, &imported_buffer_info, nullptr, &imported_buffer);
  if (result != VK_SUCCESS) {
    std::cerr << "result=fail stage=create_external_buffer vk_result=" << result
              << '\n';
    vkDestroyDevice(device, nullptr);
    vkDestroyInstance(instance, nullptr);
    return 1;
  }
  VkMemoryRequirements imported_requirements{};
  vkGetBufferMemoryRequirements(device, imported_buffer,
                                &imported_requirements);

  const VkDeviceSize host_alignment =
      std::max(host_properties.minImportedHostPointerAlignment,
               imported_requirements.alignment);
  const VkDeviceSize allocation_size =
      AlignUp(imported_requirements.size, host_alignment);
  void *host_pointer{};
  if (posix_memalign(&host_pointer, host_alignment, allocation_size) != 0) {
    std::cerr << "result=fail stage=host_allocation bytes=" << allocation_size
              << '\n';
    vkDestroyBuffer(device, imported_buffer, nullptr);
    vkDestroyDevice(device, nullptr);
    vkDestroyInstance(instance, nullptr);
    return 1;
  }
  std::memset(host_pointer, 0, allocation_size);
  std::cout << "buffer_requirement_size=" << imported_requirements.size
            << " buffer_requirement_alignment="
            << imported_requirements.alignment
            << " host_allocation_size=" << allocation_size
            << " host_pointer_alignment=" << host_alignment << '\n';

  const auto get_host_pointer_properties =
      reinterpret_cast<PFN_vkGetMemoryHostPointerPropertiesEXT>(
          vkGetDeviceProcAddr(device, "vkGetMemoryHostPointerPropertiesEXT"));
  VkMemoryHostPointerPropertiesEXT pointer_properties{
      .sType = VK_STRUCTURE_TYPE_MEMORY_HOST_POINTER_PROPERTIES_EXT,
  };
  result = get_host_pointer_properties != nullptr
               ? get_host_pointer_properties(device, HostHandle, host_pointer,
                                             &pointer_properties)
               : VK_ERROR_EXTENSION_NOT_PRESENT;
  if (result != VK_SUCCESS) {
    std::cerr << "result=fail stage=host_pointer_properties vk_result="
              << result << '\n';
    std::free(host_pointer);
    vkDestroyBuffer(device, imported_buffer, nullptr);
    vkDestroyDevice(device, nullptr);
    vkDestroyInstance(instance, nullptr);
    return 1;
  }

  const std::uint32_t compatible_types =
      pointer_properties.memoryTypeBits & imported_requirements.memoryTypeBits;
  const std::uint32_t imported_memory_type =
      FindMemoryType(memory_properties, compatible_types,
                     VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                         VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                     VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
  std::cout << "pointer_memory_type_bits=0x" << std::hex
            << pointer_properties.memoryTypeBits
            << " buffer_memory_type_bits=0x"
            << imported_requirements.memoryTypeBits
            << " compatible_memory_type_bits=0x" << compatible_types << std::dec
            << '\n';
  if (imported_memory_type == std::numeric_limits<std::uint32_t>::max()) {
    std::cerr << "result=fail stage=memory_type "
                 "reason=no_host_visible_coherent_type\n";
    std::free(host_pointer);
    vkDestroyBuffer(device, imported_buffer, nullptr);
    vkDestroyDevice(device, nullptr);
    vkDestroyInstance(instance, nullptr);
    return 1;
  }

  VkMemoryDedicatedAllocateInfo dedicated_info{
      .sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
      .buffer = imported_buffer,
  };
  VkImportMemoryHostPointerInfoEXT import_info{
      .sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT,
      .pNext = &dedicated_info,
      .handleType = HostHandle,
      .pHostPointer = host_pointer,
  };
  const VkMemoryAllocateInfo imported_allocate_info{
      .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .pNext = &import_info,
      .allocationSize = allocation_size,
      .memoryTypeIndex = imported_memory_type,
  };
  VkDeviceMemory imported_memory{};
  result = vkAllocateMemory(device, &imported_allocate_info, nullptr,
                            &imported_memory);
  if (result == VK_SUCCESS) {
    result = vkBindBufferMemory(device, imported_buffer, imported_memory, 0);
  }
  if (result != VK_SUCCESS) {
    std::cerr << "result=fail stage=import_or_bind vk_result=" << result
              << '\n';
    vkDestroyBuffer(device, imported_buffer, nullptr);
    if (imported_memory != VK_NULL_HANDLE) {
      vkFreeMemory(device, imported_memory, nullptr);
    }
    std::free(host_pointer);
    vkDestroyDevice(device, nullptr);
    vkDestroyInstance(instance, nullptr);
    return 1;
  }
  const auto imported_flags =
      memory_properties.memoryTypes[imported_memory_type].propertyFlags;
  std::cout << "imported_memory_type=" << imported_memory_type
            << " imported_memory_flags=0x" << std::hex << imported_flags
            << std::dec << '\n';

  const VkBufferCreateInfo readback_buffer_info{
      .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
      .size = TestSize,
      .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
  };
  VkBuffer readback_buffer{};
  result =
      vkCreateBuffer(device, &readback_buffer_info, nullptr, &readback_buffer);
  VkMemoryRequirements readback_requirements{};
  if (result == VK_SUCCESS) {
    vkGetBufferMemoryRequirements(device, readback_buffer,
                                  &readback_requirements);
  }
  const std::uint32_t readback_memory_type =
      FindMemoryType(memory_properties, readback_requirements.memoryTypeBits,
                     VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                         VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                     VK_MEMORY_PROPERTY_HOST_CACHED_BIT);
  const VkMemoryAllocateInfo readback_allocate_info{
      .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .allocationSize = readback_requirements.size,
      .memoryTypeIndex = readback_memory_type,
  };
  VkDeviceMemory readback_memory{};
  if (result == VK_SUCCESS &&
      readback_memory_type != std::numeric_limits<std::uint32_t>::max()) {
    result = vkAllocateMemory(device, &readback_allocate_info, nullptr,
                              &readback_memory);
  } else if (result == VK_SUCCESS) {
    result = VK_ERROR_FEATURE_NOT_PRESENT;
  }
  if (result == VK_SUCCESS) {
    result = vkBindBufferMemory(device, readback_buffer, readback_memory, 0);
  }
  void *readback_pointer{};
  if (result == VK_SUCCESS) {
    result =
        vkMapMemory(device, readback_memory, 0, TestSize, 0, &readback_pointer);
  }
  if (result != VK_SUCCESS) {
    std::cerr << "result=fail stage=create_readback vk_result=" << result
              << '\n';
    if (readback_pointer != nullptr) {
      vkUnmapMemory(device, readback_memory);
    }
    if (readback_buffer != VK_NULL_HANDLE) {
      vkDestroyBuffer(device, readback_buffer, nullptr);
    }
    if (readback_memory != VK_NULL_HANDLE) {
      vkFreeMemory(device, readback_memory, nullptr);
    }
    vkDestroyBuffer(device, imported_buffer, nullptr);
    vkFreeMemory(device, imported_memory, nullptr);
    std::free(host_pointer);
    vkDestroyDevice(device, nullptr);
    vkDestroyInstance(instance, nullptr);
    return 1;
  }

  const VkBufferCreateInfo device_buffer_info{
      .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
      .size = TestSize,
      .usage = BufferUsage,
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
  };
  VkBuffer device_buffer{};
  result = vkCreateBuffer(device, &device_buffer_info, nullptr, &device_buffer);
  VkMemoryRequirements device_requirements{};
  if (result == VK_SUCCESS) {
    vkGetBufferMemoryRequirements(device, device_buffer, &device_requirements);
  }
  const std::uint32_t device_memory_type =
      FindMemoryType(memory_properties, device_requirements.memoryTypeBits,
                     VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, 0);
  const VkMemoryAllocateInfo device_allocate_info{
      .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .allocationSize = device_requirements.size,
      .memoryTypeIndex = device_memory_type,
  };
  VkDeviceMemory device_memory{};
  if (result == VK_SUCCESS &&
      device_memory_type != std::numeric_limits<std::uint32_t>::max()) {
    result = vkAllocateMemory(device, &device_allocate_info, nullptr,
                              &device_memory);
  } else if (result == VK_SUCCESS) {
    result = VK_ERROR_FEATURE_NOT_PRESENT;
  }
  if (result == VK_SUCCESS) {
    result = vkBindBufferMemory(device, device_buffer, device_memory, 0);
  }
  if (result != VK_SUCCESS) {
    std::cerr << "result=fail stage=create_device_local vk_result=" << result
              << '\n';
    if (device_buffer != VK_NULL_HANDLE) {
      vkDestroyBuffer(device, device_buffer, nullptr);
    }
    if (device_memory != VK_NULL_HANDLE) {
      vkFreeMemory(device, device_memory, nullptr);
    }
    vkUnmapMemory(device, readback_memory);
    vkDestroyBuffer(device, readback_buffer, nullptr);
    vkFreeMemory(device, readback_memory, nullptr);
    vkDestroyBuffer(device, imported_buffer, nullptr);
    vkFreeMemory(device, imported_memory, nullptr);
    std::free(host_pointer);
    vkDestroyDevice(device, nullptr);
    vkDestroyInstance(instance, nullptr);
    return 1;
  }
  const auto device_flags =
      memory_properties.memoryTypes[device_memory_type].propertyFlags;
  std::cout << "device_memory_type=" << device_memory_type
            << " device_memory_flags=0x" << std::hex << device_flags << std::dec
            << '\n';

  const VkCommandPoolCreateInfo pool_info{
      .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
      .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
      .queueFamilyIndex = queue_family_index,
  };
  VkCommandPool command_pool{};
  result = vkCreateCommandPool(device, &pool_info, nullptr, &command_pool);
  const VkCommandBufferAllocateInfo command_allocate_info{
      .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
      .commandPool = command_pool,
      .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
      .commandBufferCount = 1,
  };
  VkCommandBuffer command_buffer{};
  if (result == VK_SUCCESS) {
    result = vkAllocateCommandBuffers(device, &command_allocate_info,
                                      &command_buffer);
  }
  const VkFenceCreateInfo fence_info{.sType =
                                         VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  VkFence fence{};
  if (result == VK_SUCCESS) {
    result = vkCreateFence(device, &fence_info, nullptr, &fence);
  }
  const VkQueryPoolCreateInfo query_pool_info{
      .sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,
      .queryType = VK_QUERY_TYPE_TIMESTAMP,
      .queryCount = 2,
  };
  VkQueryPool query_pool{};
  if (result == VK_SUCCESS && queue_family->timestampValidBits != 0) {
    result = vkCreateQueryPool(device, &query_pool_info, nullptr, &query_pool);
  } else if (result == VK_SUCCESS) {
    result = VK_ERROR_FEATURE_NOT_PRESENT;
  }
  VkQueue queue{};
  vkGetDeviceQueue(device, queue_family_index, 0, &queue);

  bool contents_passed = result == VK_SUCCESS;
  const auto started = std::chrono::steady_clock::now();
  for (std::uint32_t iteration = 0; contents_passed && iteration < Iterations;
       ++iteration) {
    const std::uint32_t cpu_value = 0x13579BDFU ^ iteration;
    const std::uint32_t gpu_value = 0xA5B6C7D8U ^ iteration;
    std::fill_n(static_cast<std::uint32_t *>(host_pointer),
                TestSize / sizeof(std::uint32_t), cpu_value);
    std::memset(readback_pointer, 0, TestSize);

    vkResetFences(device, 1, &fence);
    vkResetCommandBuffer(command_buffer, 0);
    const VkCommandBufferBeginInfo begin_info{
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    result = vkBeginCommandBuffer(command_buffer, &begin_info);

    const VkBufferMemoryBarrier host_to_copy{
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_HOST_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = imported_buffer,
        .offset = 0,
        .size = TestSize,
    };
    vkCmdPipelineBarrier(command_buffer, VK_PIPELINE_STAGE_HOST_BIT,
                         VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 1,
                         &host_to_copy, 0, nullptr);
    const VkBufferCopy copy{.size = TestSize};
    vkCmdCopyBuffer(command_buffer, imported_buffer, readback_buffer, 1, &copy);

    const VkBufferMemoryBarrier copy_to_fill{
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = imported_buffer,
        .offset = 0,
        .size = TestSize,
    };
    vkCmdPipelineBarrier(command_buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 1,
                         &copy_to_fill, 0, nullptr);
    vkCmdFillBuffer(command_buffer, imported_buffer, 0, TestSize, gpu_value);

    const VkBufferMemoryBarrier transfer_to_host[] = {
        {
            .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
            .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = imported_buffer,
            .offset = 0,
            .size = TestSize,
        },
        {
            .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
            .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = readback_buffer,
            .offset = 0,
            .size = TestSize,
        },
    };
    vkCmdPipelineBarrier(command_buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         VK_PIPELINE_STAGE_HOST_BIT, 0, 0, nullptr, 2,
                         transfer_to_host, 0, nullptr);
    if (result == VK_SUCCESS) {
      result = vkEndCommandBuffer(command_buffer);
    }
    const VkSubmitInfo submit_info{
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &command_buffer,
    };
    if (result == VK_SUCCESS) {
      result = vkQueueSubmit(queue, 1, &submit_info, fence);
    }
    if (result == VK_SUCCESS) {
      result = vkWaitForFences(device, 1, &fence, VK_TRUE, 5'000'000'000ULL);
    }
    contents_passed =
        result == VK_SUCCESS &&
        std::ranges::all_of(
            std::span{static_cast<const std::uint32_t *>(readback_pointer),
                      TestSize / sizeof(std::uint32_t)},
            [cpu_value](std::uint32_t value) { return value == cpu_value; }) &&
        std::ranges::all_of(
            std::span{static_cast<const std::uint32_t *>(host_pointer),
                      TestSize / sizeof(std::uint32_t)},
            [gpu_value](std::uint32_t value) { return value == gpu_value; });
  }
  const auto elapsed = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - started);

  std::cout << "iterations=" << Iterations
            << " cpu_to_gpu_to_readback=" << PassFail(contents_passed)
            << " gpu_to_imported_host=" << PassFail(contents_passed)
            << " elapsed_ms=" << std::fixed << std::setprecision(3)
            << elapsed.count() << " average_ms=" << elapsed.count() / Iterations
            << '\n';

  const auto run_benchmark_sample =
      [&](bool imported, VkDeviceSize size,
          std::uint32_t value) -> std::optional<BenchmarkSample> {
    const VkBuffer target_buffer = imported ? imported_buffer : device_buffer;
    vkResetFences(device, 1, &fence);
    vkResetCommandBuffer(command_buffer, 0);
    const VkCommandBufferBeginInfo begin_info{
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    VkResult sample_result = vkBeginCommandBuffer(command_buffer, &begin_info);
    if (sample_result != VK_SUCCESS) {
      return std::nullopt;
    }
    vkCmdResetQueryPool(command_buffer, query_pool, 0, 2);

    const VkBufferMemoryBarrier prepare_target{
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = static_cast<VkAccessFlags>(
            imported ? VK_ACCESS_HOST_READ_BIT
                     : VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT),
        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = target_buffer,
        .offset = 0,
        .size = size,
    };
    vkCmdPipelineBarrier(command_buffer,
                         imported ? VK_PIPELINE_STAGE_HOST_BIT
                                  : VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                         VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 1,
                         &prepare_target, 0, nullptr);
    vkCmdWriteTimestamp(command_buffer, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                        query_pool, 0);
    vkCmdFillBuffer(command_buffer, target_buffer, 0, size, value);
    vkCmdWriteTimestamp(command_buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,
                        query_pool, 1);

    if (imported) {
      const VkBufferMemoryBarrier imported_to_host{
          .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
          .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
          .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
          .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
          .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
          .buffer = imported_buffer,
          .offset = 0,
          .size = size,
      };
      vkCmdPipelineBarrier(command_buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,
                           VK_PIPELINE_STAGE_HOST_BIT, 0, 0, nullptr, 1,
                           &imported_to_host, 0, nullptr);
    } else {
      const VkBufferMemoryBarrier prepare_copy[] = {
          {
              .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
              .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
              .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
              .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .buffer = device_buffer,
              .offset = 0,
              .size = size,
          },
          {
              .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
              .srcAccessMask = VK_ACCESS_HOST_READ_BIT,
              .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
              .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .buffer = readback_buffer,
              .offset = 0,
              .size = size,
          },
      };
      vkCmdPipelineBarrier(command_buffer,
                           VK_PIPELINE_STAGE_TRANSFER_BIT |
                               VK_PIPELINE_STAGE_HOST_BIT,
                           VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 2,
                           prepare_copy, 0, nullptr);
      const VkBufferCopy copy{.size = size};
      vkCmdCopyBuffer(command_buffer, device_buffer, readback_buffer, 1, &copy);
      const VkBufferMemoryBarrier readback_to_host{
          .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
          .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
          .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
          .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
          .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
          .buffer = readback_buffer,
          .offset = 0,
          .size = size,
      };
      vkCmdPipelineBarrier(command_buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,
                           VK_PIPELINE_STAGE_HOST_BIT, 0, 0, nullptr, 1,
                           &readback_to_host, 0, nullptr);
    }

    sample_result = vkEndCommandBuffer(command_buffer);
    const VkSubmitInfo submit_info{
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &command_buffer,
    };
    const auto wall_started = std::chrono::steady_clock::now();
    if (sample_result == VK_SUCCESS) {
      sample_result = vkQueueSubmit(queue, 1, &submit_info, fence);
    }
    if (sample_result == VK_SUCCESS) {
      sample_result =
          vkWaitForFences(device, 1, &fence, VK_TRUE, 5'000'000'000ULL);
    }
    const auto wall_elapsed = std::chrono::duration<double, std::micro>(
        std::chrono::steady_clock::now() - wall_started);
    std::array<std::uint64_t, 2> timestamps{};
    if (sample_result == VK_SUCCESS) {
      sample_result = vkGetQueryPoolResults(
          device, query_pool, 0, timestamps.size(), sizeof(timestamps),
          timestamps.data(), sizeof(std::uint64_t),
          VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT);
    }
    const auto *verified_pointer = static_cast<const std::uint32_t *>(
        imported ? host_pointer : readback_pointer);
    const bool verified =
        sample_result == VK_SUCCESS &&
        std::ranges::all_of(
            std::span{verified_pointer, size / sizeof(std::uint32_t)},
            [value](std::uint32_t observed) { return observed == value; });
    if (!verified) {
      return std::nullopt;
    }

    const std::uint32_t valid_bits = queue_family->timestampValidBits;
    const std::uint64_t tick_mask =
        valid_bits == 64 ? std::numeric_limits<std::uint64_t>::max()
                         : (1ULL << valid_bits) - 1;
    const std::uint64_t elapsed_ticks =
        (timestamps[1] - timestamps[0]) & tick_mask;
    return BenchmarkSample{
        .wall_microseconds = wall_elapsed.count(),
        .gpu_fill_microseconds = static_cast<double>(elapsed_ticks) *
                                 gpu_properties.limits.timestampPeriod / 1000.0,
    };
  };

  bool benchmark_passed = contents_passed;
  std::uint32_t benchmark_value = 0x2468ACE0U;
  std::cout << "benchmark_config=sizes_kib:64,256,512 warmups_per_mode=10"
               " rounds=100 samples_per_mode=200 order=ABBA_BAAB"
               " A=shadow B=imported wall_scope=submit_to_fence"
               " gpu_scope=fill\n";
  for (const VkDeviceSize size :
       std::array<VkDeviceSize, 3>{64 * 1024, 256 * 1024, 512 * 1024}) {
    for (std::uint32_t warmup = 0; benchmark_passed && warmup < 10; ++warmup) {
      benchmark_passed =
          run_benchmark_sample(false, size, benchmark_value++).has_value() &&
          run_benchmark_sample(true, size, benchmark_value++).has_value();
    }
    std::vector<BenchmarkSample> shadow_samples;
    std::vector<BenchmarkSample> imported_samples;
    shadow_samples.reserve(BenchmarkRounds * 2);
    imported_samples.reserve(BenchmarkRounds * 2);
    for (std::uint32_t round = 0; benchmark_passed && round < BenchmarkRounds;
         ++round) {
      const std::array<bool, 4> order =
          round % 2 == 0 ? std::array{false, true, true, false}
                         : std::array{true, false, false, true};
      for (const bool imported : order) {
        const auto sample =
            run_benchmark_sample(imported, size, benchmark_value++);
        if (!sample) {
          benchmark_passed = false;
          break;
        }
        (imported ? imported_samples : shadow_samples).push_back(*sample);
      }
    }
    if (!benchmark_passed) {
      break;
    }

    const auto shadow_wall =
        Summarize(shadow_samples, &BenchmarkSample::wall_microseconds);
    const auto imported_wall =
        Summarize(imported_samples, &BenchmarkSample::wall_microseconds);
    const auto shadow_fill =
        Summarize(shadow_samples, &BenchmarkSample::gpu_fill_microseconds);
    const auto imported_fill =
        Summarize(imported_samples, &BenchmarkSample::gpu_fill_microseconds);
    const double direct_wall_delta =
        (imported_wall.median / shadow_wall.median - 1.0) * 100.0;
    const double imported_fill_delta =
        (imported_fill.median / shadow_fill.median - 1.0) * 100.0;
    std::cout << "benchmark size_kib=" << size / 1024
              << " samples_per_mode=" << shadow_samples.size()
              << " shadow_wall_median_us=" << shadow_wall.median
              << " shadow_wall_p95_us=" << shadow_wall.p95
              << " imported_wall_median_us=" << imported_wall.median
              << " imported_wall_p95_us=" << imported_wall.p95
              << " direct_wall_delta_pct=" << direct_wall_delta
              << " shadow_gpu_fill_median_us=" << shadow_fill.median
              << " shadow_gpu_fill_p95_us=" << shadow_fill.p95
              << " imported_gpu_fill_median_us=" << imported_fill.median
              << " imported_gpu_fill_p95_us=" << imported_fill.p95
              << " imported_gpu_fill_delta_pct=" << imported_fill_delta << '\n';
  }
  contents_passed = contents_passed && benchmark_passed;
  std::cout << "benchmark_result=" << PassFail(benchmark_passed) << '\n';
  std::cout << "result=" << PassFail(contents_passed) << '\n';

  vkDeviceWaitIdle(device);
  vkDestroyQueryPool(device, query_pool, nullptr);
  vkDestroyFence(device, fence, nullptr);
  vkDestroyCommandPool(device, command_pool, nullptr);
  vkDestroyBuffer(device, device_buffer, nullptr);
  vkFreeMemory(device, device_memory, nullptr);
  vkUnmapMemory(device, readback_memory);
  vkDestroyBuffer(device, readback_buffer, nullptr);
  vkFreeMemory(device, readback_memory, nullptr);
  vkDestroyBuffer(device, imported_buffer, nullptr);
  vkFreeMemory(device, imported_memory, nullptr);
  std::free(host_pointer);
  vkDestroyDevice(device, nullptr);
  vkDestroyInstance(instance, nullptr);
  return contents_passed ? 0 : 1;
}
