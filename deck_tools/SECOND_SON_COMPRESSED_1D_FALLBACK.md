# Second Son compressed 1D Vulkan fallback

Issue: [#106](https://github.com/deucebucket/shadPS4/issues/106)

## Purpose

Second Son creates sampled BC1, BC4, and BC5 guest 1D resources. NVIDIA's Vulkan driver rejects
those formats as native `VK_IMAGE_TYPE_1D` images even after optional storage usage and
block-compatible views are removed. Continuing to allocate an image after
`vkGetPhysicalDeviceImageFormatProperties2` rejects its configuration is not a correctness
contract.

This fallback keeps the original BC format and compressed bytes. It changes only the host image
dimensionality on devices that cannot represent the guest resource natively.

## Proven host result

`deck_tools/vulkan_image_probe.cpp` queries the Cartesian product of:

- every BC format shadPS4 maps: BC1/BC2/BC3/BC4/BC5/BC6/BC7, including SRGB, signed,
  unsigned, and float variants as applicable;
- 1D and 2D image types;
- mutable format on/off;
- extended usage on/off;
- block-compatible views on/off; and
- storage usage on/off.

On an RTX 3090 with NVIDIA 580.159.04, all 224 compressed 1D combinations returned
`VK_ERROR_FORMAT_NOT_SUPPORTED`. Every corresponding 2D format was supported once its requested
usage and flags were valid. The complete 910-line local oracle receipt has SHA-256:

```text
0a97d95cfb13b9e0feadf897e9442ad6ecaa2be650ef61a984a275e8f9893af8
```

Two consecutive runs produced that exact hash. The receipt is local evidence and is not committed
because it also enumerates host devices and memory heaps. Heap metadata is intentionally limited
to stable size/type fields; live VRAM usage would make the capability receipt nondeterministic.

## Runtime contract

At device initialization, shadPS4 queries every block-compressed entry in its canonical surface
format table using the irreducible transfer-source, transfer-destination, and sampled usage plus
the normal mutable and extended-usage flags. Keeping the capability loop coupled to that table
prevents a future BC format mapping from silently falling outside the fallback contract.

- If all queries succeed, the existing native compressed 1D path is unchanged.
- If any query fails, shader resource tracking marks only block-compressed guest 1D resources for
  promotion. Runtime image allocation independently repeats that decision from the texture
  descriptor currently bound by the guest. It does not trust persistent shader metadata, which
  may outlive a resource-type permutation.
- A guest 1D/1D-array resource becomes a host 2D/2D-array image with one logical row. Format,
  compressed payload, width, mip count, array layers, component mapping, and transfer extents stay
  unchanged.
- Normalized sampling uses Y=`0.5`; integer fetch/storage coordinates and offsets use Y=`0`;
  explicit Y gradients are zero. This preserves X-only filtering and LOD selection.
- Guest dimension queries still report 1D semantics. The synthetic host row is never exposed to
  the guest shader.
- A fail-fast assertion rejects any promoted descriptor whose nominal guest height is not one.

## Local oracle

Build against the repository's Vulkan headers and a system Vulkan loader, then save the output in
ignored scratch storage:

```sh
c++ -std=c++20 -Wall -Wextra -Wpedantic -Werror \
  -Iexternals/vulkan-headers/include \
  deck_tools/vulkan_image_probe.cpp -o scratch/vulkan_image_probe -l:libvulkan.so.1
scratch/vulkan_image_probe > scratch/vulkan_image_probe.txt
```

The probe exits nonzero for Vulkan query errors other than the expected success and
format-not-supported results.

## Runtime proof

The touched translation units compile and link under the repository's GCC 15/C++23 Linux flags. A
bounded 60-second CUSA00223 v1.00 boot capture on the proven host selected the fallback and reached
the planned timeout without an assertion or image allocation, format-support, or view error. The
instrumented capture exercised these actual guest descriptors:

- BC1 UNORM and SRGB `Color1D`, 1x1;
- BC4 and BC5 UNORM `Color1D`, 1x1; and
- BC1 SRGB `Color1DArray`, 512x1.

Every promoted descriptor was unit-height. No `Color2D` or `Color3D` resource was promoted. That
negative result is important: an earlier prototype derived the runtime choice from cached shader
metadata and exposed a stale-permutation bug by incorrectly treating 1920x1080 uncompressed 2D
resources as fallback candidates. The production path derives its runtime choice from the live
texture descriptor instead, while the shader permutation retains its own compile-time choice for
coordinate and view translation.

The per-resource proof log is debug-only. Normal runs record one device-level capability decision
without flooding the render log. A later game-only 2560x1440 framebuffer capture reached playable
gameplay and showed the full scene—including foliage, fire, vehicle materials, road detail, text,
and the player character—without missing rows, striped corruption, black textures, or another
visible compressed-texture failure. The owned-game screenshot remains in ignored local evidence;
it is not committed to the public repository.
