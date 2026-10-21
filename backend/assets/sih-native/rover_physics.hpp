#pragma once

#include "flight_core.hpp"
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace xovium::rover {
// The canonical height field is shared by native conversions. Heights are ENU
// metres, row-major (east changes fastest), relative to origin[2].
class HeightField {
    struct Dynamic;
    std::shared_ptr<Dynamic> dynamic_;
    std::filesystem::path path_;
    std::filesystem::file_time_type modified_{};
    std::vector<double> heights_;
    std::array<double, 3> origin_{};
    std::array<double, 2> spacing_{1, 1};
    std::size_t width_ = 0, height_ = 0;
public:
    explicit HeightField(const std::string& path = {});
    void stream(const std::string& config);
    void export_tiles(const std::string& directory);
    bool ready(double east, double north);
    void reload();
    double elevation(double east, double north) const;
};

struct Parameters {
    double mass = 20, track = 0.3, wheelbase = 0.45, clearance = 0.18;
    double max_speed = 2.1, max_yaw_rate = 2.5, motor_tau = 0.15, motor_rate = 4, max_acceleration = 5;
    double friction = 0.8, rolling_resistance = 0.025, yaw_inertia = 0.6;
    int right_channel = 0, left_channel = 1;
};
Parameters load_parameters(const std::string& path);

class Dynamics {
    Parameters config_;
    HeightField terrain_;
    px4isaac::VehicleState state_;
    double heading_ = 0, speed_ = 0, yaw_rate_ = 0;
    std::array<double, 2> motors_{};
    px4isaac::Quat attitude(double east, double north, double heading) const;
public:
    Dynamics(Parameters parameters, HeightField terrain, const px4isaac::Config& config);
    void step(double right, double left, double dt, bool armed);
    void reload_terrain() { terrain_.reload(); }
    bool terrain_ready() { return terrain_.ready(state_.position_enu[0], state_.position_enu[1]); }
    const px4isaac::VehicleState& state() const { return state_; }
};
}
