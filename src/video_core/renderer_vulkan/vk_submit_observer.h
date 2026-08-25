// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/types.h"

namespace Vulkan {

class SubmitObserver {
public:
    virtual void OnCommandBufferSubmit(u64 submitted_tick) = 0;

protected:
    virtual ~SubmitObserver() = default;
};

} // namespace Vulkan
