// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/arch.h"
#include "common/signal_context.h"

#ifdef _WIN32
#include <windows.h>
#elif defined(__FreeBSD__)
#include <machine/npx.h>
#include <sys/ucontext.h>
#else
#include <sys/ucontext.h>
#endif

namespace Common {

FaultContext GetFaultContext(void* ctx) {
#if defined(_WIN32)
    const auto& regs = *((EXCEPTION_POINTERS*)ctx)->ContextRecord;
    return {
        .rip = regs.Rip,
        .rax = regs.Rax,
        .rcx = regs.Rcx,
        .rdx = regs.Rdx,
        .rsi = regs.Rsi,
        .rdi = regs.Rdi,
        .rbp = regs.Rbp,
        .rsp = regs.Rsp,
    };
#elif defined(__APPLE__) && defined(ARCH_X86_64)
    const auto& regs = ((ucontext_t*)ctx)->uc_mcontext->__ss;
    return {
        .rip = regs.__rip,
        .rax = regs.__rax,
        .rcx = regs.__rcx,
        .rdx = regs.__rdx,
        .rsi = regs.__rsi,
        .rdi = regs.__rdi,
        .rbp = regs.__rbp,
        .rsp = regs.__rsp,
    };
#elif defined(__APPLE__) && defined(ARCH_ARM64)
    return {.rip = ((ucontext_t*)ctx)->uc_mcontext->__ss.__pc};
#elif defined(__FreeBSD__)
    const auto& regs = ((ucontext_t*)ctx)->uc_mcontext;
    return {
        .rip = regs.mc_rip,
        .rax = regs.mc_rax,
        .rcx = regs.mc_rcx,
        .rdx = regs.mc_rdx,
        .rsi = regs.mc_rsi,
        .rdi = regs.mc_rdi,
        .rbp = regs.mc_rbp,
        .rsp = regs.mc_rsp,
    };
#elif defined(ARCH_X86_64)
    const auto& regs = ((ucontext_t*)ctx)->uc_mcontext.gregs;
    return {
        .rip = static_cast<VAddr>(regs[REG_RIP]),
        .rax = static_cast<VAddr>(regs[REG_RAX]),
        .rcx = static_cast<VAddr>(regs[REG_RCX]),
        .rdx = static_cast<VAddr>(regs[REG_RDX]),
        .rsi = static_cast<VAddr>(regs[REG_RSI]),
        .rdi = static_cast<VAddr>(regs[REG_RDI]),
        .rbp = static_cast<VAddr>(regs[REG_RBP]),
        .rsp = static_cast<VAddr>(regs[REG_RSP]),
    };
#else
#error "Unsupported architecture"
#endif
}

void* GetRip(void* ctx) {
#if defined(_WIN32)
    return (void*)((EXCEPTION_POINTERS*)ctx)->ContextRecord->Rip;
#elif defined(__APPLE__) && defined(ARCH_X86_64)
    return (void*)((ucontext_t*)ctx)->uc_mcontext->__ss.__rip;
#elif defined(__APPLE__) && defined(ARCH_ARM64)
    return (void*)((ucontext_t*)ctx)->uc_mcontext->__ss.__pc;
#elif defined(__FreeBSD__)
    return (void*)((ucontext_t*)ctx)->uc_mcontext.mc_rip;
#elif defined(ARCH_X86_64)
    return (void*)((ucontext_t*)ctx)->uc_mcontext.gregs[REG_RIP];
#else
#error "Unsupported architecture"
#endif
}

bool IsWriteError(void* ctx) {
#if defined(_WIN32)
    return ((EXCEPTION_POINTERS*)ctx)->ExceptionRecord->ExceptionInformation[0] == 1;
#elif defined(__APPLE__) && defined(ARCH_X86_64)
    return ((ucontext_t*)ctx)->uc_mcontext->__es.__err & 0x2;
#elif defined(__APPLE__) && defined(ARCH_ARM64)
    return ((ucontext_t*)ctx)->uc_mcontext->__es.__esr & 0x40;
#elif defined(__FreeBSD__) && defined(ARCH_X86_64)
    return ((ucontext_t*)ctx)->uc_mcontext.mc_err & 0x2;
#elif defined(ARCH_X86_64)
    return ((ucontext_t*)ctx)->uc_mcontext.gregs[REG_ERR] & 0x2;
#else
#error "Unsupported architecture"
#endif
}

} // namespace Common
