#pragma once

#include "flight_core.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace px4isaac {

// PX4 differential rover motor functions: 101 is right; 102 is left.
// Commands remain signed throughout the HIL path, including reverse/pivot turns.
class RoverDrive {
public:
    explicit RoverDrive(const Config::Rover& config) : config_(config) {}
    std::array<double, 2> step(const std::array<double, 2>& targets, bool armed, double dt) {
        if (!std::isfinite(dt) || dt <= 0) throw std::invalid_argument("Rover timestep must be positive");
        for (double value : targets) if (!std::isfinite(value)) throw std::invalid_argument("Rover command must be finite");
        // Full opposite motors would request almost 13 rad/s on this narrow
        // rover. Limit the differential to its configured steering speed.
        const double limit = std::min(1.0, config_.maximum_yaw_rate_rad_s * config_.wheel_track_m / (2 * config_.maximum_speed_m_s));
        const double difference = std::clamp((targets[0]-targets[1])/2, -limit, limit);
        const double mean = std::clamp((targets[0]+targets[1])/2, -1+std::abs(difference), 1-std::abs(difference));
        const std::array<double,2> limited{mean+difference,mean-difference};
        for (std::size_t i = 0; i < 2; ++i) {
            if (!armed) { commands_[i] = 0; continue; }
            const double change = (std::clamp(limited[i], -1.0, 1.0) - commands_[i])
                * -std::expm1(-dt / config_.motor_time_constant_s);
            commands_[i] += std::clamp(change, -config_.motor_rate_per_s * dt, config_.motor_rate_per_s * dt);
        }
        return commands_;
    }
    double degrees_per_second(std::size_t side) const {
        // Positive body +Y rotation rolls forward (+X) at the ground contact.
        return commands_.at(side) * config_.maximum_speed_m_s / config_.wheel_radius_m * 180.0 / 3.14159265358979323846;
    }
private:
    Config::Rover config_;
    std::array<double, 2> commands_{};
};

}
