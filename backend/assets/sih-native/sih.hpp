#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <common/mavlink.h>

struct SihPose {
    double position[3];
    double orientation[4];
    double received_at;
    std::uint64_t count;
};

namespace xovium::sih {
using Vec3 = std::array<double, 3>;
using Quat = std::array<double, 4>;
Quat ned_frd_to_enu_flu(const Quat& quaternion);
Vec3 geodetic_to_enu(const Vec3& position, const Vec3& origin);
double monotonic_seconds();

class Tracker {
    Vec3 origin_;
    std::uint8_t system_id_;
    SihPose pose_{};
public:
    Tracker(Vec3 origin, std::uint8_t system_id);
    bool update(const mavlink_message_t& message, double received_at);
    const SihPose& pose() const { return pose_; }
};

class Receiver {
    struct Impl;
    std::unique_ptr<Impl> impl_;
public:
    Receiver(const char* address, std::uint16_t port, Vec3 origin, std::uint8_t system_id);
    ~Receiver();
    Receiver(const Receiver&) = delete;
    Receiver& operator=(const Receiver&) = delete;
    bool snapshot(SihPose& pose);
    std::uint16_t port() const;
};
}

extern "C" {
void* sih_open(const char* address, std::uint16_t port, std::uint8_t system_id,
               const double* origin, char* error, std::size_t error_size) noexcept;
int sih_snapshot(void* receiver, SihPose* pose, char* error, std::size_t error_size) noexcept;
void sih_close(void* receiver) noexcept;
}
