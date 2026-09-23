#include "flight_core.hpp"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <system_error>
#include <vector>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <nlohmann/json.hpp>
#include <common/mavlink.h>

namespace px4isaac {
namespace {
constexpr double pi = 3.14159265358979323846;
constexpr std::array<const char*, 4> control_names{"aileron", "elevator", "rudder", "throttle"};
using Clock = std::chrono::steady_clock;

template <std::size_t N>
void finite_vector(const std::array<double, N>& value, const char* name) {
    for (double component : value) {
        if (!std::isfinite(component)) throw std::invalid_argument(std::string(name) + " must be finite");
    }
}

void require_positive(double value, const std::string& name) {
    if (!std::isfinite(value) || value <= 0) throw std::invalid_argument(name + " must be positive and finite");
}

double magnitude(const Vec3& vector) {
    return std::hypot(vector[0], vector[1], vector[2]);
}

Controls bounded_controls(const Controls& input) {
    finite_vector(input, "Controls");
    Controls result{};
    for (std::size_t i = 0; i < result.size(); ++i) result[i] = std::clamp(input[i], i == 3 ? 0.0 : -1.0, 1.0);
    return result;
}

template <typename T>
T clipped_integer(double value) {
    return static_cast<T>(std::clamp(std::round(value), static_cast<double>(std::numeric_limits<T>::lowest()),
                                   static_cast<double>(std::numeric_limits<T>::max())));
}

Vec3 vector_from_json(const nlohmann::json& value, const char* name) {
    if (!value.is_array() || value.size() != 3) throw std::invalid_argument(std::string(name) + " must contain three values");
    Vec3 result{};
    for (std::size_t i = 0; i < 3; ++i) {
        if (!value[i].is_number()) throw std::invalid_argument(std::string(name) + " must contain numbers");
        result[i] = value[i].get<double>();
    }
    finite_vector(result, name);
    return result;
}

void socket_nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
        throw std::system_error(errno, std::generic_category(), "Cannot set socket nonblocking");
}

}

Config load_config(const std::string& path) {
    std::ifstream stream(path);
    if (!stream) throw std::runtime_error("Cannot open aircraft configuration: " + path);
    const auto document = nlohmann::json::parse(stream);
    Config config;
    config.name = document.value("name", "Fixed-wing aircraft");
    config.mass_kg = document.at("mass_kg").get<double>();
    config.inertia_kg_m2 = vector_from_json(document.at("inertia_kg_m2"), "inertia_kg_m2");
    config.wing_area_m2 = document.at("wing_area_m2").get<double>();
    config.wingspan_m = document.at("wingspan_m").get<double>();
    config.mean_chord_m = document.at("mean_chord_m").get<double>();
    config.coefficients = document.at("coefficients").get<decltype(config.coefficients)>();
    config.propulsion = document.at("propulsion").get<decltype(config.propulsion)>();
    config.actuators = document.at("actuators").get<decltype(config.actuators)>();
    const auto& channels = document.at("actuator_channels");
    if (!channels.is_object() || channels.size() != 4) throw std::invalid_argument("actuator_channels must define four controls");
    for (std::size_t i = 0; i < 4; ++i) {
        const auto& channel = channels.at(control_names[i]);
        if (!channel.is_number_integer()) throw std::invalid_argument("Actuator channels must be integers");
        const auto number = channel.get<std::int64_t>();
        if (number < 0 || number >= 16) throw std::invalid_argument("Actuator channels must be from 0 through 15");
        config.actuator_channels[i] = static_cast<int>(number);
    }
    config.spawn_position_enu_m = vector_from_json(document.at("spawn").at("position_enu_m"), "spawn.position_enu_m");
    config.spawn_heading_deg = document.at("spawn").at("heading_deg").get<double>();
    if (document.contains("wind_enu_m_s")) config.wind_enu_m_s = vector_from_json(document.at("wind_enu_m_s"), "wind_enu_m_s");
    if (document.contains("sensors")) {
        const auto& sensors = document.at("sensors");
        config.sensors.accel_noise_std_m_s2 = sensors.value("accel_noise_std_m_s2", config.sensors.accel_noise_std_m_s2);
        config.sensors.gyro_noise_std_rad_s = sensors.value("gyro_noise_std_rad_s", config.sensors.gyro_noise_std_rad_s);
        config.sensors.mag_noise_std_gauss = sensors.value("mag_noise_std_gauss", config.sensors.mag_noise_std_gauss);
        config.sensors.baro_noise_std_hpa = sensors.value("baro_noise_std_hpa", config.sensors.baro_noise_std_hpa);
        if (sensors.contains("magnetic_field_ned_gauss"))
            config.sensors.magnetic_field_ned_gauss = vector_from_json(sensors.at("magnetic_field_ned_gauss"), "magnetic_field_ned_gauss");
    }
    validate_config(config);
    return config;
}

void validate_config(const Config& c) {
    require_positive(c.mass_kg, "mass_kg");
    require_positive(c.wing_area_m2, "wing_area_m2");
    require_positive(c.wingspan_m, "wingspan_m");
    require_positive(c.mean_chord_m, "mean_chord_m");
    finite_vector(c.inertia_kg_m2, "inertia_kg_m2");
    const double inertia_sum = c.inertia_kg_m2[0] + c.inertia_kg_m2[1] + c.inertia_kg_m2[2];
    for (double moment : c.inertia_kg_m2) {
        if (moment <= 0 || 2 * moment > inertia_sum + 1e-9) throw std::invalid_argument("Inertia must satisfy the triangle inequality");
    }
    for (const auto* group : {&c.coefficients, &c.propulsion, &c.actuators}) {
        for (const auto& [name, value] : *group) {
            if (!std::isfinite(value)) throw std::invalid_argument(name + " must be finite");
        }
    }
    const auto& a = c.coefficients;
    for (const char* name : {"cl_zero", "cl_alpha", "cl_max", "cl_post_stall", "stall_start_deg", "stall_end_deg",
         "cd_zero", "cd_induced", "cd_post_stall", "cd_reverse", "cy_beta", "cy_rudder", "roll_beta", "roll_p",
         "roll_r", "roll_aileron", "pitch_zero", "pitch_alpha", "pitch_q", "pitch_elevator", "yaw_beta", "yaw_p",
         "yaw_r", "yaw_rudder"}) {
        if (!a.count(name)) throw std::invalid_argument(std::string("Missing aircraft coefficient: ") + name);
    }
    if (!(0 < a.at("stall_start_deg") && a.at("stall_start_deg") < a.at("stall_end_deg") && a.at("stall_end_deg") < 90))
        throw std::invalid_argument("Stall angles must satisfy 0 < start < end < 90 degrees");
    for (const char* name : {"cl_max", "cl_post_stall", "cl_alpha"}) require_positive(a.at(name), name);
    for (const char* name : {"cd_zero", "cd_induced", "cd_post_stall", "cd_reverse"}) {
        if (a.at(name) < 0) throw std::invalid_argument(std::string(name) + " must be nonnegative");
    }
    for (const char* name : {"max_thrust_n", "zero_thrust_speed_m_s"}) require_positive(c.propulsion.at(name), name);
    for (const char* name : {"servo_time_constant_s", "servo_rate_per_s", "motor_time_constant_s", "motor_rate_per_s"})
        require_positive(c.actuators.at(name), name);
    finite_vector(c.spawn_position_enu_m, "Spawn position");
    finite_vector(c.wind_enu_m_s, "Wind");
    finite_vector(c.sensors.magnetic_field_ned_gauss, "Magnetic field");
    if (!std::isfinite(c.spawn_heading_deg)) throw std::invalid_argument("Spawn heading must be finite");
    for (std::size_t i = 0; i < 4; ++i) {
        if (c.actuator_channels[i] < 0 || c.actuator_channels[i] >= 16) throw std::invalid_argument("Actuator channels must be from 0 through 15");
        for (std::size_t j = 0; j < i; ++j) {
            if (c.actuator_channels[i] == c.actuator_channels[j]) throw std::invalid_argument("Actuator channels must be distinct");
        }
    }
    for (double deviation : {c.sensors.accel_noise_std_m_s2, c.sensors.gyro_noise_std_rad_s,
                            c.sensors.mag_noise_std_gauss, c.sensors.baro_noise_std_hpa}) {
        if (!std::isfinite(deviation) || deviation < 0) throw std::invalid_argument("Sensor noise deviations must be finite and nonnegative");
    }
}

Quat normalized_quaternion(const Quat& value) {
    finite_vector(value, "Quaternion");
    const double length = std::hypot(std::hypot(value[0], value[1]), std::hypot(value[2], value[3]));
    if (length < 1e-12 || !std::isfinite(length)) throw std::invalid_argument("Quaternion has invalid length");
    Quat result{};
    for (std::size_t i = 0; i < 4; ++i) result[i] = value[i] / length;
    return result;
}

Quat quaternion_product(const Quat& a, const Quat& b) {
    return {a[0]*b[0] - a[1]*b[1] - a[2]*b[2] - a[3]*b[3],
            a[0]*b[1] + a[1]*b[0] + a[2]*b[3] - a[3]*b[2],
            a[0]*b[2] - a[1]*b[3] + a[2]*b[0] + a[3]*b[1],
            a[0]*b[3] + a[1]*b[2] - a[2]*b[1] + a[3]*b[0]};
}

Vec3 rotate(const Quat& quaternion, const Vec3& vector) {
    const auto q = normalized_quaternion(quaternion);
    const auto result = quaternion_product(quaternion_product(q, {0, vector[0], vector[1], vector[2]}),
                                           {q[0], -q[1], -q[2], -q[3]});
    return {result[1], result[2], result[3]};
}

Vec3 inverse_rotate(const Quat& quaternion, const Vec3& vector) {
    const auto q = normalized_quaternion(quaternion);
    return rotate({q[0], -q[1], -q[2], -q[3]}, vector);
}

Vec3 flu_to_frd(const Vec3& v) { return {v[0], -v[1], -v[2]}; }
Vec3 enu_to_ned(const Vec3& v) { return {v[1], v[0], -v[2]}; }

Quat attitude_ned_frd(const Quat& quaternion) {
    auto result = normalized_quaternion(quaternion_product(quaternion_product({0, std::sqrt(0.5), std::sqrt(0.5), 0}, quaternion),
                                                           {0, 1, 0, 0}));
    if (result[0] < 0) for (auto& component : result) component = -component;
    return result;
}

Quat heading_quaternion(double heading_degrees) {
    if (!std::isfinite(heading_degrees)) throw std::invalid_argument("Heading must be finite");
    const double half_angle = (90 - heading_degrees) * pi / 360;
    return {std::cos(half_angle), 0, 0, std::sin(half_angle)};
}

Vec3 enu_to_geodetic(const Vec3& position, const Vec3& origin) {
    finite_vector(position, "Position");
    finite_vector(origin, "Geographic origin");
    const double lat = origin[0] * pi / 180;
    const double lon = origin[1] * pi / 180;
    constexpr double eccentricity_squared = 6.6943799901413165e-3;
    double radius = 6378137.0 / std::sqrt(1 - eccentricity_squared * std::sin(lat) * std::sin(lat));
    double x = (radius + origin[2]) * std::cos(lat) * std::cos(lon);
    double y = (radius + origin[2]) * std::cos(lat) * std::sin(lon);
    double z = (radius * (1 - eccentricity_squared) + origin[2]) * std::sin(lat);
    x += -std::sin(lon)*position[0] - std::sin(lat)*std::cos(lon)*position[1] + std::cos(lat)*std::cos(lon)*position[2];
    y += std::cos(lon)*position[0] - std::sin(lat)*std::sin(lon)*position[1] + std::cos(lat)*std::sin(lon)*position[2];
    z += std::cos(lat)*position[1] + std::sin(lat)*position[2];
    const double horizontal = std::hypot(x, y);
    double current_lat = std::atan2(z, horizontal * (1 - eccentricity_squared));
    for (int i = 0; i < 8; ++i) {
        radius = 6378137.0 / std::sqrt(1 - eccentricity_squared * std::sin(current_lat) * std::sin(current_lat));
        current_lat = std::atan2(z + eccentricity_squared * radius * std::sin(current_lat), horizontal);
    }
    radius = 6378137.0 / std::sqrt(1 - eccentricity_squared * std::sin(current_lat) * std::sin(current_lat));
    return {current_lat * 180 / pi, std::atan2(y, x) * 180 / pi, horizontal / std::cos(current_lat) - radius};
}

Atmosphere atmosphere(double altitude_m) {
    if (!std::isfinite(altitude_m) || altitude_m < -1000 || altitude_m > 20000)
        throw std::invalid_argument("Atmosphere supports altitude between -1000 and 20000 metres");
    const double temperature_k = 288.15 - 0.0065 * std::min(altitude_m, 11000.0);
    double pressure_pa = 101325 * std::pow(temperature_k / 288.15, gravity / (287.05 * 0.0065));
    if (altitude_m > 11000) pressure_pa *= std::exp(-gravity * (altitude_m - 11000) / (287.05 * temperature_k));
    return {pressure_pa / 100, temperature_k - 273.15, pressure_pa / (287.05 * temperature_k)};
}

Airframe::Airframe(const Config& config) : config_(config) { validate_config(config_); }
const Controls& Airframe::controls() const { return controls_; }
void Airframe::reset_controls() { controls_.fill(0); }

Controls Airframe::filter_controls(const Controls& input, double dt) {
    if (!std::isfinite(dt) || dt < 0) throw std::invalid_argument("dt must be nonnegative and finite");
    const auto targets = bounded_controls(input);
    for (std::size_t i = 0; i < 4; ++i) {
        const std::string prefix = i == 3 ? "motor" : "servo";
        const double tau = config_.actuators.at(prefix + "_time_constant_s");
        const double step = (targets[i] - controls_[i]) * -std::expm1(-dt / tau);
        const double limit = config_.actuators.at(prefix + "_rate_per_s") * dt;
        controls_[i] += std::clamp(step, -limit, limit);
    }
    return controls_;
}

Forces Airframe::evaluate(const Vec3& velocity, const Vec3& rates, double density, const Controls& input) const {
    finite_vector(velocity, "Body velocity");
    finite_vector(rates, "Body rates");
    require_positive(density, "Air density");
    const auto control = bounded_controls(input);
    const auto& a = config_.coefficients;
    const double u = velocity[0], left = velocity[1], up = velocity[2];
    const double speed = magnitude(velocity);
    const double longitudinal_speed = std::hypot(u, up);
    const double alpha = longitudinal_speed > 1e-9 ? std::atan2(-up, u) : 0;
    const double beta = speed > 1e-9 ? std::asin(std::clamp(-left / speed, -1.0, 1.0)) : 0;
    const double pressure_area = 0.5 * density * speed * speed * config_.wing_area_m2;
    const double stall_start = a.at("stall_start_deg") * pi / 180;
    const double stall_end = a.at("stall_end_deg") * pi / 180;
    double blend = std::clamp((std::abs(alpha) - stall_start) / (stall_end - stall_start), 0.0, 1.0);
    blend = blend * blend * (3 - 2 * blend);
    const double attached_lift = std::clamp(a.at("cl_zero") + a.at("cl_alpha") * alpha, -a.at("cl_max"), a.at("cl_max"));
    const double separated_lift = a.at("cl_post_stall") * std::sin(2 * alpha);
    const double lift_coefficient = (1 - blend) * attached_lift + blend * separated_lift;
    const double drag_coefficient = a.at("cd_zero") + a.at("cd_induced") * lift_coefficient * lift_coefficient
        + blend * a.at("cd_post_stall") * std::sin(alpha) * std::sin(alpha) + a.at("cd_reverse") * (1 - std::cos(alpha));
    const double drag = pressure_area * drag_coefficient;
    Vec3 force{};
    if (speed > 1e-9) for (std::size_t i = 0; i < 3; ++i) force[i] = -drag * velocity[i] / speed;
    const double lift = pressure_area * lift_coefficient;
    if (longitudinal_speed > 1e-9) {
        force[0] -= lift * up / longitudinal_speed;
        force[2] += lift * u / longitudinal_speed;
    }
    force[1] -= pressure_area * (a.at("cy_beta") * beta + a.at("cy_rudder") * control[2]);
    const double advance = std::max(0.0, u) / config_.propulsion.at("zero_thrust_speed_m_s");
    force[0] += config_.propulsion.at("max_thrust_n") * control[3] * std::max(0.0, 1 - advance * advance);
    const double denominator = 2 * std::max(speed, 1.0);
    const double p = rates[0] * config_.wingspan_m / denominator;
    const double q = -rates[1] * config_.mean_chord_m / denominator;
    const double r = -rates[2] * config_.wingspan_m / denominator;
    const double roll = a.at("roll_beta")*beta + a.at("roll_p")*p + a.at("roll_r")*r + a.at("roll_aileron")*control[0];
    const double pitch = a.at("pitch_zero") + a.at("pitch_alpha")*std::sin(alpha)*std::cos(alpha)
        + a.at("pitch_q")*q + a.at("pitch_elevator")*control[1];
    const double yaw = a.at("yaw_beta")*beta + a.at("yaw_p")*p + a.at("yaw_r")*r + a.at("yaw_rudder")*control[2];
    return {force, {pressure_area*config_.wingspan_m*roll, -pressure_area*config_.mean_chord_m*pitch,
                    -pressure_area*config_.wingspan_m*yaw}};
}

SensorModel::SensorModel(const Config& config, Vec3 origin, std::uint32_t seed)
    : origin_(origin), wind_enu_(config.wind_enu_m_s), magnetic_field_enu_(enu_to_ned(config.sensors.magnetic_field_ned_gauss)),
      config_(config.sensors), random_(seed) {
    finite_vector(origin_, "Geographic origin");
    if (origin_[0] <= -89 || origin_[0] >= 89 || origin_[1] < -180 || origin_[1] > 180)
        throw std::invalid_argument("Invalid geographic origin");
    finite_vector(wind_enu_, "Wind");
    finite_vector(magnetic_field_enu_, "Magnetic field");
    for (double deviation : {config_.accel_noise_std_m_s2, config_.gyro_noise_std_rad_s,
                            config_.mag_noise_std_gauss, config_.baro_noise_std_hpa}) {
        if (!std::isfinite(deviation) || deviation < 0) throw std::invalid_argument("Invalid sensor noise deviation");
    }
    atmosphere(origin_[2]);
}

const Vec3& SensorModel::origin() const { return origin_; }

SensorReading SensorModel::sample(const VehicleState& state) {
    finite_vector(state.position_enu, "Position");
    finite_vector(state.velocity_enu, "Velocity");
    finite_vector(state.acceleration_enu, "Acceleration");
    finite_vector(state.angular_velocity_flu, "Angular velocity");
    const auto quaternion = normalized_quaternion(state.quaternion_wxyz);
    const Vec3 specific_force{state.acceleration_enu[0], state.acceleration_enu[1], state.acceleration_enu[2] + gravity};
    const auto acceleration = flu_to_frd(inverse_rotate(quaternion, specific_force));
    const auto gyro = flu_to_frd(state.angular_velocity_flu);
    auto magnetic_field = flu_to_frd(inverse_rotate(quaternion, magnetic_field_enu_));
    const auto geographic = enu_to_geodetic(state.position_enu, origin_);
    const auto air = atmosphere(geographic[2]);
    Vec3 relative_velocity{};
    for (std::size_t i = 0; i < 3; ++i) relative_velocity[i] = state.velocity_enu[i] - wind_enu_[i];
    const double forward_airspeed = std::max(0.0, inverse_rotate(quaternion, relative_velocity)[0]);
    const double differential_pressure = 0.5 * air.density_kg_m3 * forward_airspeed * forward_airspeed;
    const double noisy_pressure = air.pressure_hpa + noise_(random_) * config_.baro_noise_std_hpa;
    const double pressure_altitude = 44330.77 * (1 - std::pow(std::max(noisy_pressure, 0.001) / 1013.25, 0.190263));
    auto noisy_acceleration = acceleration;
    auto noisy_gyro = gyro;
    for (std::size_t i = 0; i < 3; ++i) noisy_acceleration[i] += noise_(random_) * config_.accel_noise_std_m_s2;
    for (std::size_t i = 0; i < 3; ++i) noisy_gyro[i] += noise_(random_) * config_.gyro_noise_std_rad_s;
    for (std::size_t i = 0; i < 3; ++i) magnetic_field[i] += noise_(random_) * config_.mag_noise_std_gauss;
    return {noisy_acceleration, noisy_gyro, magnetic_field, noisy_pressure, differential_pressure / 100,
            pressure_altitude, air.temperature_c, geographic[0], geographic[1], geographic[2], enu_to_ned(state.velocity_enu),
            attitude_ned_frd(quaternion), magnitude(relative_velocity), std::sqrt(2*differential_pressure/1.225), acceleration, gyro};
}

struct MavlinkBridge::Impl {
    SensorModel sensors;
    int listener = -1;
    int client = -1;
    std::uint16_t port = 0;
    double control_timeout_s;
    bool connected = false;
    bool armed = false;
    bool received_controls = false;
    std::array<double, 16> controls{};
    std::string failure;
    std::uint64_t control_timestamp_us = 0;
    std::uint64_t sensor_timestamp_us = 0;
    std::uint64_t last_gps_us = 0;
    std::uint64_t last_truth_us = 0;
    std::uint64_t last_heartbeat_us = 0;
    Clock::time_point sensor_sent_wall_time{};
    std::vector<std::uint8_t> send_buffer;
    mavlink_message_t receive_buffer{};
    mavlink_status_t receive_status{};
    mavlink_status_t send_status{};

    Impl(SensorModel model, double timeout) : sensors(std::move(model)), control_timeout_s(timeout) {}
    ~Impl() {
        if (client >= 0) ::close(client);
        if (listener >= 0) ::close(listener);
    }

    void disconnect(const std::string& reason) {
        failure = reason;
        connected = false;
        armed = false;
        controls.fill(0);
        send_buffer.clear();
        if (client >= 0) ::close(client);
        client = -1;
    }

    void queue(const mavlink_message_t& message) {
        if (!connected) return;
        std::array<std::uint8_t, MAVLINK_MAX_PACKET_LEN> buffer{};
        const auto length = mavlink_msg_to_send_buffer(buffer.data(), &message);
        if (send_buffer.size() + length > 524288) {
            disconnect("PX4 stopped reading simulation data");
            return;
        }
        send_buffer.insert(send_buffer.end(), buffer.begin(), buffer.begin() + length);
    }

    void flush() {
        if (client < 0 || send_buffer.empty()) return;
        const auto count = ::send(client, send_buffer.data(), send_buffer.size(), MSG_NOSIGNAL);
        if (count > 0) send_buffer.erase(send_buffer.begin(), send_buffer.begin() + count);
        else if (count == 0) disconnect("PX4 disconnected from the simulator");
        else if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
            disconnect(std::string("PX4 simulation send failed: ") + std::strerror(errno));
    }

    void receive(const mavlink_message_t& message) {
        if (message.msgid != MAVLINK_MSG_ID_HIL_ACTUATOR_CONTROLS) return;
        mavlink_hil_actuator_controls_t actuator{};
        mavlink_msg_hil_actuator_controls_decode(&message, &actuator);
        if (actuator.time_usec < control_timestamp_us || actuator.time_usec == 0) return;
        for (float value : actuator.controls) {
            if (!std::isfinite(value)) {
                disconnect("PX4 sent invalid actuator controls");
                return;
            }
        }
        received_controls = true;
        control_timestamp_us = actuator.time_usec;
        armed = (actuator.mode & MAV_MODE_FLAG_SAFETY_ARMED) != 0;
        for (std::size_t i = 0; i < 16; ++i) controls[i] = armed ? std::clamp(static_cast<double>(actuator.controls[i]), -1.0, 1.0) : 0;
    }
};

MavlinkBridge::MavlinkBridge(SensorModel sensors, std::uint16_t port, const std::string& host, double timeout)
    : impl_(std::make_unique<Impl>(std::move(sensors), timeout)) {
    require_positive(timeout, "Actuator timeout");
    auto& p = *impl_;
    p.listener = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (p.listener < 0) throw std::system_error(errno, std::generic_category(), "Cannot create simulation socket");
    const int enabled = 1;
    if (::setsockopt(p.listener, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled)) < 0)
        throw std::system_error(errno, std::generic_category(), "Cannot configure simulation socket");
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    if (::inet_pton(AF_INET, host.c_str(), &address.sin_addr) != 1) throw std::invalid_argument("Simulator host must be an IPv4 address");
    if (::bind(p.listener, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) < 0)
        throw std::system_error(errno, std::generic_category(), "Cannot reserve simulator TCP " + host + ":" + std::to_string(port));
    if (::listen(p.listener, 1) < 0) throw std::system_error(errno, std::generic_category(), "Cannot listen for PX4");
    socket_nonblocking(p.listener);
    socklen_t length = sizeof(address);
    if (::getsockname(p.listener, reinterpret_cast<sockaddr*>(&address), &length) < 0)
        throw std::system_error(errno, std::generic_category(), "Cannot read simulator TCP port");
    p.port = ntohs(address.sin_port);
}

MavlinkBridge::~MavlinkBridge() = default;
bool MavlinkBridge::connected() const { return impl_->connected; }
bool MavlinkBridge::armed() const { return impl_->armed; }
bool MavlinkBridge::received_controls() const { return impl_->received_controls; }
const std::array<double, 16>& MavlinkBridge::controls() const { return impl_->controls; }
const std::string& MavlinkBridge::failure() const { return impl_->failure; }
std::uint16_t MavlinkBridge::port() const { return impl_->port; }
std::uint64_t MavlinkBridge::control_timestamp_us() const { return impl_->control_timestamp_us; }
std::uint64_t MavlinkBridge::sensor_timestamp_us() const { return impl_->sensor_timestamp_us; }

bool MavlinkBridge::ready_to_step() const {
    return impl_->connected && impl_->send_buffer.empty()
        && (!impl_->received_controls || impl_->control_timestamp_us >= impl_->sensor_timestamp_us);
}

void MavlinkBridge::close() {
    impl_->disconnect(impl_->failure.empty() ? "Simulator bridge closed" : impl_->failure);
    if (impl_->listener >= 0) ::close(impl_->listener);
    impl_->listener = -1;
}

void MavlinkBridge::poll() {
    auto& p = *impl_;
    if (!p.failure.empty()) return;
    if (p.client < 0) {
        p.client = ::accept4(p.listener, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
        if (p.client < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
                p.disconnect(std::string("Cannot accept PX4 connection: ") + std::strerror(errno));
            return;
        }
        const int enabled = 1;
        if (::setsockopt(p.client, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled)) < 0) {
            p.disconnect(std::string("Cannot configure PX4 connection: ") + std::strerror(errno));
            return;
        }
        p.connected = true;
    }
    p.flush();
    for (int i = 0; i < 64 && p.client >= 0; ++i) {
        std::array<std::uint8_t, 4096> data{};
        const auto count = ::recv(p.client, data.data(), data.size(), 0);
        if (count < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
                p.disconnect(std::string("PX4 simulation receive failed: ") + std::strerror(errno));
            break;
        }
        if (count == 0) {
            p.disconnect("PX4 disconnected; restart PX4 and the simulator together");
            break;
        }
        for (ssize_t index = 0; index < count; ++index) {
            mavlink_message_t message{};
            mavlink_status_t status{};
            const auto framing = mavlink_frame_char_buffer(&p.receive_buffer, &p.receive_status, data[index], &message, &status);
            if (framing == MAVLINK_FRAMING_OK) p.receive(message);
            if (!p.failure.empty()) return;
        }
    }
    if (p.connected && p.received_controls && p.control_timestamp_us < p.sensor_timestamp_us
        && std::chrono::duration<double>(Clock::now() - p.sensor_sent_wall_time).count() > p.control_timeout_s)
        p.disconnect("PX4 actuator timeout; simulation stopped without reusing stale throttle");
}

void MavlinkBridge::publish(const VehicleState& state, std::uint64_t time_us) {
    auto& p = *impl_;
    if (!p.connected) throw std::runtime_error("PX4 is not connected to the simulator");
    if (time_us <= p.sensor_timestamp_us) throw std::invalid_argument("Simulation timestamps must be positive and strictly increasing");
    const auto r = p.sensors.sample(state);
    mavlink_message_t message{};
    if (p.last_heartbeat_us == 0 || time_us - p.last_heartbeat_us >= 1000000) {
        mavlink_msg_heartbeat_pack_status(1, 200, &p.send_status, &message, MAV_TYPE_GENERIC, MAV_AUTOPILOT_INVALID, 0, 0, MAV_STATE_ACTIVE);
        p.queue(message);
        p.last_heartbeat_us = time_us;
    }
    const auto latitude = clipped_integer<std::int32_t>(r.latitude * 1e7);
    const auto longitude = clipped_integer<std::int32_t>(r.longitude * 1e7);
    const auto altitude = clipped_integer<std::int32_t>(r.altitude_m * 1000);
    const auto vn = clipped_integer<std::int16_t>(r.velocity_ned[0] * 100);
    const auto ve = clipped_integer<std::int16_t>(r.velocity_ned[1] * 100);
    const auto vd = clipped_integer<std::int16_t>(r.velocity_ned[2] * 100);
    if (p.last_gps_us == 0 || time_us - p.last_gps_us >= 100000) {
        const double speed = std::hypot(r.velocity_ned[0], r.velocity_ned[1]);
        const auto course = speed > 0.1
            ? static_cast<std::uint16_t>((static_cast<int>(std::round(std::atan2(r.velocity_ned[1], r.velocity_ned[0]) * 18000 / pi)) % 36000 + 36000) % 36000)
            : static_cast<std::uint16_t>(65535);
        mavlink_msg_hil_gps_pack_status(1, 200, &p.send_status, &message, time_us, 3, latitude, longitude, altitude,
                                       30, 50, clipped_integer<std::uint16_t>(speed * 100), vn, ve, vd, course, 16, 0, 0);
        p.queue(message);
        p.last_gps_us = time_us;
    }
    if (p.last_truth_us == 0 || time_us - p.last_truth_us >= 20000) {
        std::array<float, 4> quaternion{};
        std::transform(r.quaternion_ned_frd.begin(), r.quaternion_ned_frd.end(), quaternion.begin(), [](double v) { return static_cast<float>(v); });
        mavlink_msg_hil_state_quaternion_pack_status(1, 200, &p.send_status, &message, time_us, quaternion.data(),
            r.truth_angular_velocity_frd[0], r.truth_angular_velocity_frd[1], r.truth_angular_velocity_frd[2],
            latitude, longitude, altitude, vn, ve, vd, clipped_integer<std::uint16_t>(r.indicated_airspeed_m_s * 100),
            clipped_integer<std::uint16_t>(r.true_airspeed_m_s * 100),
            clipped_integer<std::int16_t>(r.truth_acceleration_frd[0] / gravity * 1000),
            clipped_integer<std::int16_t>(r.truth_acceleration_frd[1] / gravity * 1000),
            clipped_integer<std::int16_t>(r.truth_acceleration_frd[2] / gravity * 1000));
        p.queue(message);
        p.last_truth_us = time_us;
    }
    mavlink_msg_hil_sensor_pack_status(1, 200, &p.send_status, &message, time_us,
        r.acceleration_frd[0], r.acceleration_frd[1], r.acceleration_frd[2], r.angular_velocity_frd[0], r.angular_velocity_frd[1],
        r.angular_velocity_frd[2], r.magnetic_field_frd[0], r.magnetic_field_frd[1], r.magnetic_field_frd[2], r.pressure_hpa,
        r.differential_pressure_hpa, r.pressure_altitude_m, r.temperature_c, 0x1FFF, 0);
    p.queue(message);
    p.sensor_timestamp_us = time_us;
    p.sensor_sent_wall_time = Clock::now();
    p.flush();
}

}
