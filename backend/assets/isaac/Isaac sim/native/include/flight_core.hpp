#pragma once

#include <array>
#include <cstdint>
#include <memory>
#include <random>
#include <string>
#include <unordered_map>

namespace px4isaac {

using Vec3 = std::array<double, 3>;
using Quat = std::array<double, 4>;
using Controls = std::array<double, 4>;
inline constexpr double gravity = 9.80665;

struct SensorConfig {
    double accel_noise_std_m_s2 = 0.02;
    double gyro_noise_std_rad_s = 0.001;
    double mag_noise_std_gauss = 0.0005;
    double baro_noise_std_hpa = 0.01;
    Vec3 magnetic_field_ned_gauss{0.215, 0.011, 0.427};
};

struct Config {
    std::string name;
    double mass_kg = 1.5;
    Vec3 inertia_kg_m2{0.19, 0.08, 0.25};
    double wing_area_m2 = 0.45;
    double wingspan_m = 1.5;
    double mean_chord_m = 0.3;
    std::unordered_map<std::string, double> coefficients;
    std::unordered_map<std::string, double> propulsion;
    std::unordered_map<std::string, double> actuators;
    std::array<int, 4> actuator_channels{0, 1, 2, 3};
    Vec3 spawn_position_enu_m{0, 0, 0.31};
    double spawn_heading_deg = 0;
    Vec3 wind_enu_m_s{0, 0, 0};
    SensorConfig sensors;
};

Config load_config(const std::string& path);
void validate_config(const Config& config);
Quat normalized_quaternion(const Quat& value);
Quat quaternion_product(const Quat& a, const Quat& b);
Vec3 rotate(const Quat& quaternion, const Vec3& vector);
Vec3 inverse_rotate(const Quat& quaternion, const Vec3& vector);
Vec3 flu_to_frd(const Vec3& vector);
Vec3 enu_to_ned(const Vec3& vector);
Quat attitude_ned_frd(const Quat& quaternion);
Quat heading_quaternion(double heading_degrees);
Vec3 enu_to_geodetic(const Vec3& position, const Vec3& origin);

struct Atmosphere {
    double pressure_hpa;
    double temperature_c;
    double density_kg_m3;
};

Atmosphere atmosphere(double altitude_m);

struct Forces {
    Vec3 force_flu;
    Vec3 torque_flu;
};

class Airframe {
public:
    explicit Airframe(const Config& config);
    Controls filter_controls(const Controls& targets, double dt);
    void reset_controls();
    Forces evaluate(const Vec3& velocity_flu, const Vec3& rates_flu,
                    double density, const Controls& controls) const;
    const Controls& controls() const;
private:
    Config config_;
    Controls controls_{};
};

struct VehicleState {
    Vec3 position_enu{};
    Quat quaternion_wxyz{1, 0, 0, 0};
    Vec3 velocity_enu{};
    Vec3 angular_velocity_flu{};
    Vec3 acceleration_enu{};
};

struct SensorReading {
    Vec3 acceleration_frd;
    Vec3 angular_velocity_frd;
    Vec3 magnetic_field_frd;
    double pressure_hpa;
    double differential_pressure_hpa;
    double pressure_altitude_m;
    double temperature_c;
    double latitude;
    double longitude;
    double altitude_m;
    Vec3 velocity_ned;
    Quat quaternion_ned_frd;
    double true_airspeed_m_s;
    double indicated_airspeed_m_s;
    Vec3 truth_acceleration_frd;
    Vec3 truth_angular_velocity_frd;
};

class SensorModel {
public:
    explicit SensorModel(const Config& config, Vec3 origin = {47.397742, 8.545594, 489.4},
                         std::uint32_t seed = 0);
    SensorReading sample(const VehicleState& state);
    const Vec3& origin() const;
private:
    Vec3 origin_;
    Vec3 wind_enu_;
    Vec3 magnetic_field_enu_;
    SensorConfig config_;
    std::mt19937 random_;
    std::normal_distribution<double> noise_{0, 1};
};

class MavlinkBridge {
public:
    explicit MavlinkBridge(SensorModel sensors, std::uint16_t port = 4560,
                           const std::string& host = "127.0.0.1", double control_timeout_s = 30.0);
    ~MavlinkBridge();
    MavlinkBridge(const MavlinkBridge&) = delete;
    MavlinkBridge& operator=(const MavlinkBridge&) = delete;
    void poll();
    void publish(const VehicleState& state, std::uint64_t sim_time_us);
    void close();
    bool ready_to_step() const;
    bool connected() const;
    bool armed() const;
    bool received_controls() const;
    const std::array<double, 16>& controls() const;
    const std::string& failure() const;
    std::uint16_t port() const;
    std::uint64_t control_timestamp_us() const;
    std::uint64_t sensor_timestamp_us() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}
