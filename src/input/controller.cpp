// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cmath>
#include <cstring>
#include <mutex>
#include <unordered_set>
#include <utility>

#include <SDL3/SDL.h>

#include "common/logging/log.h"
#include "core/emulator_settings.h"
#include "core/libraries/kernel/time.h"
#include "core/libraries/system/userservice.h"
#include "core/user_settings.h"
#include "input/controller.h"

namespace Input {

using Libraries::Pad::OrbisPadButtonDataOffset;

namespace {

constexpr u64 SpraySidewaysEnd = 2'000'000;
constexpr u64 SprayShakeEnd = 5'000'000;
constexpr u64 SpraySettleEnd = 5'750'000;
constexpr u64 SprayPaintEnd = 45'750'000;

void CalculateOrientation(const Libraries::Pad::OrbisFVector3& angular_velocity, float delta_time,
                          const Libraries::Pad::OrbisFQuaternion& last_orientation,
                          Libraries::Pad::OrbisFQuaternion& orientation) {
    if (delta_time > 1.0f) {
        orientation = last_orientation;
        return;
    }
    Libraries::Pad::OrbisFQuaternion q = last_orientation;
    const Libraries::Pad::OrbisFQuaternion omega = {angular_velocity.x, angular_velocity.y,
                                                    angular_velocity.z, 0.0f};

    const Libraries::Pad::OrbisFQuaternion q_omega = {
        q.w * omega.x + q.x * omega.w + q.y * omega.z - q.z * omega.y,
        q.w * omega.y + q.y * omega.w + q.z * omega.x - q.x * omega.z,
        q.w * omega.z + q.z * omega.w + q.x * omega.y - q.y * omega.x,
        q.w * omega.w - q.x * omega.x - q.y * omega.y - q.z * omega.z};

    const Libraries::Pad::OrbisFQuaternion q_dot = {0.5f * q_omega.x, 0.5f * q_omega.y,
                                                    0.5f * q_omega.z, 0.5f * q_omega.w};

    q.x += q_dot.x * delta_time;
    q.y += q_dot.y * delta_time;
    q.z += q_dot.z * delta_time;
    q.w += q_dot.w * delta_time;

    const float norm = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
    q.x /= norm;
    q.y /= norm;
    q.z /= norm;
    q.w /= norm;
    orientation = q;
}

} // namespace

GameController::GameController() : m_states_queue(64) {}

State GameController::ReadState() {
    std::lock_guard lock{m_state_mutex};
    return m_state;
}

int GameController::ReadStates(State* states, int states_num) {
    std::lock_guard lock{m_state_mutex};
    if (states_num <= 0) {
        return 0;
    }

    if (!m_state.connected) {
        states[0] = m_state;
        return 1;
    }

    if (states_num == 1) {
        // Retained history can make a later multi-sample read return up to 64 stale reports, so
        // mixed single- and multi-sample reads require dedicated tests.
        states[0] = m_state;
        return 1;
    }

    int read_count = 0;
    while (read_count < states_num) {
        auto state = m_states_queue.Pop();
        if (!state) {
            break;
        }
        states[read_count++] = std::move(*state);
    }
    return read_count;
}

void GameController::Button(OrbisPadButtonDataOffset button, bool is_pressed) {
    std::lock_guard lock{m_state_mutex};
    m_state.OnButton(button, is_pressed);
    PushStateLocked();
}

void GameController::Axis(Input::Axis axis, int value, bool smooth) {
    std::lock_guard lock{m_state_mutex};
    const u64 timestamp = Libraries::Kernel::sceKernelGetProcessTime();
    if (axis == Input::Axis::TriggerRight) {
        raw_trigger_right = value;
        if (!spray_assist_active) {
            m_state.OnAxis(axis, value, timestamp, smooth);
            if (motion_override == 3 && value > 0) {
                motion_override = 4;
            } else if (motion_override == 4 && value == 0) {
                motion_override = 0;
            }
        }
    } else {
        m_state.OnAxis(axis, value, timestamp, smooth);
    }
    PushStateLocked(timestamp);
}

void GameController::UpdateGyro(const float gyro[3]) {
    std::lock_guard lock{m_state_mutex};
    std::memcpy(raw_gyro_buf, gyro, sizeof(raw_gyro_buf));
}

void GameController::UpdateAcceleration(const float acceleration[3]) {
    std::lock_guard lock{m_state_mutex};
    std::memcpy(raw_accel_buf, acceleration, sizeof(raw_accel_buf));
}

void GameController::SetMotionOverride(s8 mode) {
    std::lock_guard lock{m_state_mutex};
    // After a synthetic rattle, temporarily translate the right stick into gyro motion. This lets
    // a handheld controller cover a motion-only stencil while the right trigger is held, then
    // restores ordinary right-stick behavior as soon as the trigger is released.
    if (motion_override == 2 && mode == 0) {
        m_state.orientation = {0.0f, 0.0f, 0.0f, 1.0f};
        m_last_orientation_update = 0;
        motion_override = 3;
    } else {
        motion_override = mode;
    }
}

void GameController::StartSprayAssist() {
    std::lock_guard lock{m_state_mutex};
    const u64 timestamp = Libraries::Kernel::sceKernelGetProcessTime();
    spray_assist_start = timestamp;
    spray_assist_active = true;
    motion_override = 0;
    m_state.orientation = {0.0f, 0.0f, 0.0f, 1.0f};
    m_last_orientation_update = 0;
    ApplyMotionInputLocked(timestamp);
    PushStateLocked(timestamp);
    LOG_INFO(Input, "Controller spray assist started");
}

void GameController::PollState() {
    std::lock_guard lock{m_state_mutex};
    const u64 timestamp = Libraries::Kernel::sceKernelGetProcessTime();
    ApplyMotionInputLocked(timestamp);
    PushStateLocked(timestamp);
}

void GameController::ResetOrientation() {
    std::lock_guard lock{m_state_mutex};
    m_state.orientation = {0.0f, 0.0f, 0.0f, 1.0f};
    m_last_orientation_update = 0;
    PushStateLocked();
}

void GameController::SetTouchpadState(int touch_index, bool touch_down, float x, float y) {
    if (touch_index < 0 || touch_index >= 2) {
        return;
    }

    std::lock_guard lock{m_state_mutex};
    const u64 timestamp = Libraries::Kernel::sceKernelGetProcessTime();
    const bool was_pressed = m_state.touchpad[0].state || m_state.touchpad[1].state;
    auto& touch = m_state.touchpad[touch_index];
    if (touch_down && !touch.state) {
        touch.ID = m_next_touch_id;
        m_next_touch_id = m_next_touch_id == 127 ? 1 : m_next_touch_id + 1;
    }
    m_state.OnTouchpad(touch_index, touch_down, x, y);
    const bool is_pressed = m_state.touchpad[0].state || m_state.touchpad[1].state;
    if (!was_pressed && is_pressed) {
        m_touch_down_timestamp = timestamp;
    } else if (was_pressed && !is_pressed) {
        m_touch_down_timestamp = 0;
    }
    PushStateLocked(timestamp);
}

void GameController::ConnectController(SDL_Gamepad* pad) {
    std::lock_guard lock{m_state_mutex};
    m_sdl_gamepad = pad;
    m_states_queue.Clear();
    if (!m_state.connected) {
        ++m_state.connected_count;
        if (m_state.connected_count == 0) {
            m_state.connected_count = 1;
        }
    }
    m_state.connected = true;
    m_last_orientation_update = 0;
    PushStateLocked();
}

void GameController::DisconnectController() {
    std::lock_guard lock{m_state_mutex};
    m_states_queue.Clear();
    m_sdl_gamepad = nullptr;

    const u8 connected_count = m_state.connected_count;
    m_state = {};
    m_state.connected_count = connected_count;
    std::fill(gyro_buf, gyro_buf + 3, 0.0f);
    std::fill(accel_buf, accel_buf + 3, 0.0f);
    accel_buf[1] = 9.81f;
    std::fill(raw_gyro_buf, raw_gyro_buf + 3, 0.0f);
    std::fill(raw_accel_buf, raw_accel_buf + 3, 0.0f);
    raw_accel_buf[1] = 9.81f;
    raw_trigger_right = 0;
    spray_assist_start = 0;
    spray_assist_active = false;
    motion_override = 0;
    m_next_touch_id = 1;
    m_touch_down_timestamp = 0;
    m_state.connected = false;
    m_last_orientation_update = 0;
    PushStateLocked();
}

void GameController::ApplyMotionInputLocked(u64 timestamp) {
    if (spray_assist_active) {
        const u64 elapsed = timestamp >= spray_assist_start ? timestamp - spray_assist_start : 0;
        const bool painting = elapsed >= SpraySettleEnd && elapsed < SprayPaintEnd;
        m_state.OnAxis(Input::Axis::TriggerRight, painting ? 255 : 0, timestamp, false);

        if (elapsed < SpraySidewaysEnd) {
            constexpr float sideways_accel[3] = {-9.81f, 0.0f, 0.0f};
            constexpr float still_gyro[3] = {0.0f, 0.0f, 0.0f};
            std::memcpy(accel_buf, sideways_accel, sizeof(accel_buf));
            std::memcpy(gyro_buf, still_gyro, sizeof(gyro_buf));
        } else if (elapsed < SprayShakeEnd) {
            const float direction = ((elapsed / 100'000) & 1) != 0 ? 1.0f : -1.0f;
            const float shake_accel[3] = {-9.81f + direction * 30.0f, 0.0f, 0.0f};
            const float shake_gyro[3] = {0.0f, 0.0f, direction * 3.0f};
            std::memcpy(accel_buf, shake_accel, sizeof(accel_buf));
            std::memcpy(gyro_buf, shake_gyro, sizeof(gyro_buf));
        } else if (elapsed < SpraySettleEnd) {
            constexpr float sideways_accel[3] = {-9.81f, 0.0f, 0.0f};
            constexpr float still_gyro[3] = {0.0f, 0.0f, 0.0f};
            std::memcpy(accel_buf, sideways_accel, sizeof(accel_buf));
            std::memcpy(gyro_buf, still_gyro, sizeof(gyro_buf));
        } else if (elapsed < SprayPaintEnd) {
            constexpr float sideways_accel[3] = {-9.81f, 0.0f, 0.0f};
            const float paint_time = (elapsed - SpraySettleEnd) / 1'000'000.0f;
            const float scan_direction =
                (static_cast<u64>(paint_time / 0.7f) & 1) != 0 ? 1.0f : -1.0f;
            const float paint_gyro[3] = {
                scan_direction * 0.9f,
                std::sin(paint_time * 0.43f) * 0.22f,
                std::sin(paint_time * 0.61f) * 0.65f,
            };
            std::memcpy(accel_buf, sideways_accel, sizeof(accel_buf));
            std::memcpy(gyro_buf, paint_gyro, sizeof(gyro_buf));
        } else {
            FinishSprayAssistLocked(timestamp);
        }
        return;
    }

    std::memcpy(gyro_buf, raw_gyro_buf, sizeof(gyro_buf));
    std::memcpy(accel_buf, raw_accel_buf, sizeof(accel_buf));
    if (motion_override == 2) {
        const float direction = ((timestamp / 100'000) & 1) != 0 ? 1.0f : -1.0f;
        const float shake_gyro[3] = {0.0f, 0.0f, direction * 3.0f};
        const float shake_accel[3] = {-9.81f + direction * 30.0f, 0.0f, 0.0f};
        std::memcpy(gyro_buf, shake_gyro, sizeof(gyro_buf));
        std::memcpy(accel_buf, shake_accel, sizeof(accel_buf));
    } else if (motion_override == -1 || motion_override == 1) {
        const float sideways_accel[3] = {motion_override * 9.81f, 0.0f, 0.0f};
        std::memcpy(accel_buf, sideways_accel, sizeof(accel_buf));
    } else if (motion_override == 3 || motion_override == 4) {
        const float stick_x =
            (128.0f - m_state.axes[static_cast<int>(Input::Axis::RightX)]) / 127.0f;
        const float stick_y =
            (128.0f - m_state.axes[static_cast<int>(Input::Axis::RightY)]) / 127.0f;
        const float time = timestamp / 1'000'000.0f;
        const bool auto_sweep =
            motion_override == 4 && std::abs(stick_x) < 0.05f && std::abs(stick_y) < 0.05f;
        const float aim_gyro[3] = {
            raw_gyro_buf[0] + stick_y * 0.6f +
                (auto_sweep ? std::sin(time * 1.4f) * 0.45f : 0.0f),
            raw_gyro_buf[1] + stick_x * 0.18f +
                (auto_sweep ? std::sin(time * 0.8f) * 0.08f : 0.0f),
            raw_gyro_buf[2] + stick_y * 0.6f +
                (auto_sweep ? std::cos(time * 1.1f) * 0.45f : 0.0f),
        };
        constexpr float sideways_accel[3] = {-9.81f, 0.0f, 0.0f};
        std::memcpy(gyro_buf, aim_gyro, sizeof(gyro_buf));
        std::memcpy(accel_buf, sideways_accel, sizeof(accel_buf));
    }
}

void GameController::FinishSprayAssistLocked(u64 timestamp) {
    spray_assist_active = false;
    spray_assist_start = 0;
    m_state.OnAxis(Input::Axis::TriggerRight, raw_trigger_right, timestamp, false);
    std::memcpy(gyro_buf, raw_gyro_buf, sizeof(gyro_buf));
    std::memcpy(accel_buf, raw_accel_buf, sizeof(accel_buf));
    m_state.orientation = {0.0f, 0.0f, 0.0f, 1.0f};
    m_last_orientation_update = timestamp;
    LOG_INFO(Input, "Controller spray assist completed");
}

void GameController::UpdateOrientationLocked(u64 timestamp) {
    if (m_last_orientation_update == 0 || timestamp <= m_last_orientation_update) {
        m_last_orientation_update = timestamp;
        return;
    }
    const float delta_time =
        static_cast<float>(timestamp - m_last_orientation_update) / 1'000'000.f;
    Libraries::Pad::OrbisFQuaternion orientation{};
    CalculateOrientation(m_state.angularVelocity, delta_time, m_state.orientation, orientation);
    m_state.orientation = orientation;
    m_last_orientation_update = timestamp;
}

void GameController::PushStateLocked(u64 timestamp) {
    if (timestamp == 0) {
        timestamp = Libraries::Kernel::sceKernelGetProcessTime();
    }
    m_state.UpdateAxisSmoothing(timestamp);
    m_state.OnGyro(gyro_buf);
    m_state.OnAccel(accel_buf);
    UpdateOrientationLocked(timestamp);
    m_state.time = timestamp;
    m_state.touch_time_since_held_down =
        m_touch_down_timestamp == 0 ? 0 : timestamp - m_touch_down_timestamp;
    m_states_queue.Push(m_state);
}

void GameController::SetLightBarRGB(u8 const r, u8 const g, u8 const b) {
    if (override_colour.has_value()) {
        return;
    }
    colour = {r, g, b};
    if (m_sdl_gamepad != nullptr) {
        SDL_SetGamepadLED(m_sdl_gamepad, r, g, b);
    }
}

void GameController::SetLightBarRGB(Colour const c) {
    SetLightBarRGB(c.r, c.g, c.b);
}

Colour GameController::GetLightBarRGB() {
    return colour;
}

void GameController::PollLightColour() {
    if (m_sdl_gamepad != nullptr) {
        SDL_SetGamepadLED(m_sdl_gamepad, colour.r, colour.g, colour.b);
    }
}

void GameControllers::ResetLightbarColors() {
    for (auto& c : controllers) {
        auto const* u = UserManagement.GetUserByID(c->user_id);
        if (!u || !c->m_sdl_gamepad) {
            continue;
        }
        auto const i = u->user_color - 1;
        if (i < 0 || i > 3) {
            continue;
        }
        auto const& col = g_user_colours[i];
        c->override_colour = std::nullopt;
        c->SetLightBarRGB(col);
    }
}

bool GameController::SetVibration(u8 smallMotor, u8 largeMotor) {
    if (m_sdl_gamepad != nullptr) {
        return SDL_RumbleGamepad(m_sdl_gamepad, (smallMotor / 255.0f) * 0xFFFF,
                                 (largeMotor / 255.0f) * 0xFFFF, -1);
    }
    return true;
}

static bool is_first_check = true;

void GameControllers::TryOpenSDLControllers() {
    using namespace Libraries::UserService;
    int controller_count;
    SDL_JoystickID* new_joysticks = SDL_GetGamepads(&controller_count);
    LOG_INFO(Input, "{} controllers are currently connected", controller_count);

    std::unordered_set<SDL_JoystickID> assigned_ids;
    std::array<bool, 4> slot_taken{false, false, false, false};

    for (int i = 0; i < 4; i++) {
        SDL_Gamepad* pad = controllers[i]->m_sdl_gamepad;
        if (pad) {
            SDL_JoystickID id = SDL_GetGamepadID(pad);
            bool still_connected = false;
            for (int j = 0; j < controller_count; j++) {
                if (new_joysticks[j] == id) {
                    still_connected = true;
                    assigned_ids.insert(id);
                    slot_taken[i] = true;
                    break;
                }
            }
            if (!still_connected) {
                SDL_CloseGamepad(pad);
                controllers[i]->DisconnectController();
                controllers[i]->user_id = -1;
                slot_taken[i] = false;
            }
        }
    }

    for (int j = 0; j < controller_count; j++) {
        SDL_JoystickID id = new_joysticks[j];
        if (assigned_ids.contains(id))
            continue;

        SDL_Gamepad* pad = SDL_OpenGamepad(id);
        if (!pad) {
            continue;
        }

        for (int i = 0; i < 4; i++) {
            if (!slot_taken[i]) {
                auto u = UserManagement.GetUserByPlayerIndex(i + 1);
                if (!u) {
                    LOG_INFO(Input, "User {} not found", i + 1);
                    continue; // for now, if you don't specify who Player N is in the config,
                              // Player N won't be registered at all
                }
                auto* c = controllers[i];
                LOG_INFO(Input, "Gamepad registered for slot {}! Handle: {}", i,
                         SDL_GetGamepadID(pad));
                slot_taken[i] = true;
                c->user_id = u->user_id;
                UserManagement.LoginUser(u, i + 1);
                c->ConnectController(pad);
                if (EmulatorSettings.IsMotionControlsEnabled()) {
                    if (SDL_SetGamepadSensorEnabled(c->m_sdl_gamepad, SDL_SENSOR_GYRO, true)) {
                        const float poll_rate =
                            SDL_GetGamepadSensorDataRate(c->m_sdl_gamepad, SDL_SENSOR_GYRO);
                        LOG_INFO(Input, "Gyro initialized, poll rate: {}", poll_rate);
                    } else {
                        LOG_ERROR(Input, "Failed to initialize gyro controls for gamepad {}",
                                  c->user_id);
                    }
                    if (SDL_SetGamepadSensorEnabled(c->m_sdl_gamepad, SDL_SENSOR_ACCEL, true)) {
                        const float poll_rate =
                            SDL_GetGamepadSensorDataRate(c->m_sdl_gamepad, SDL_SENSOR_ACCEL);
                        LOG_INFO(Input, "Accel initialized, poll rate: {}", poll_rate);
                    } else {
                        LOG_ERROR(Input, "Failed to initialize accel controls for gamepad {}",
                                  c->user_id);
                    }
                }
                break;
            }
        }
    }
    if (is_first_check) [[unlikely]] {
        is_first_check = false;
        if (controller_count == 0) {
            auto u = UserManagement.GetUserByPlayerIndex(1);
            controllers[0]->user_id = u->user_id;
            controllers[0]->ConnectController(nullptr);
            UserManagement.LoginUser(u, 1);
        }
    }
    SDL_free(new_joysticks);
}
u8 GameControllers::GetGamepadIndexFromJoystickId(SDL_JoystickID id) {
    auto g = SDL_GetGamepadFromID(id);
    ASSERT(g != nullptr);
    for (int i = 0; i < 5; i++) {
        if (controllers[i]->m_sdl_gamepad == g) {
            return i;
        }
    }
    // LOG_TRACE(Input, "Gamepad index: {}", index);
    return -1;
}

std::optional<u8> GameControllers::GetControllerIndexFromUserID(s32 user_id) {
    auto const u = UserManagement.GetUserByID(user_id);
    if (!u) {
        return std::nullopt;
    }
    return u->player_index - 1;
}

std::optional<u8> GameControllers::GetControllerIndexFromControllerID(s32 controller_id) {
    if (controller_id < 1 || controller_id > 5) {
        return std::nullopt;
    }
    return controller_id - 1;
}

} // namespace Input
