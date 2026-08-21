// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/types.h"

namespace Common {

struct FaultContext {
    VAddr rip{};
    VAddr rax{};
    VAddr rcx{};
    VAddr rdx{};
    VAddr rsi{};
    VAddr rdi{};
    VAddr rbp{};
    VAddr rsp{};
};

void* GetXmmPointer(void* ctx, u8 index);

void* GetRip(void* ctx);

FaultContext GetFaultContext(void* ctx);

void IncrementRip(void* ctx, u64 length);

bool IsWriteError(void* ctx);

} // namespace Common
