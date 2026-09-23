#include "sih.hpp"
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <arpa/inet.h>
#include <poll.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

namespace xovium::sih {
namespace {
constexpr double pi = 3.14159265358979323846;
void validate(const Vec3& p) {
    if (!std::all_of(p.begin(), p.end(), [](double x) { return std::isfinite(x); }) ||
        std::abs(p[0]) > 90 || std::abs(p[1]) > 180)
        throw std::invalid_argument("Invalid latitude/longitude/altitude");
}
Quat normalized(Quat q) {
    if (!std::all_of(q.begin(), q.end(), [](double x) { return std::isfinite(x); }))
        throw std::invalid_argument("Non-finite quaternion");
    const double length = std::hypot(std::hypot(q[0], q[1]), std::hypot(q[2], q[3]));
    if (!std::isfinite(length) || length < 1e-12)
        throw std::invalid_argument("Zero/invalid quaternion");
    for (auto& value : q) value /= length;
    return q;
}
Quat product(const Quat& a, const Quat& b) {
    return {a[0]*b[0]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3],
            a[0]*b[1]+a[1]*b[0]+a[2]*b[3]-a[3]*b[2],
            a[0]*b[2]-a[1]*b[3]+a[2]*b[0]+a[3]*b[1],
            a[0]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[0]};
}
Vec3 ecef(const Vec3& p) {
    const double lat = p[0]*pi/180, lon = p[1]*pi/180;
    constexpr double e2 = 6.6943799901413165e-3;
    const double r = 6378137.0 / std::sqrt(1-e2*std::sin(lat)*std::sin(lat));
    return {(r+p[2])*std::cos(lat)*std::cos(lon), (r+p[2])*std::cos(lat)*std::sin(lon),
            (r*(1-e2)+p[2])*std::sin(lat)};
}
std::runtime_error system_error(const char* operation) {
    return std::runtime_error(std::string(operation) + ": " + std::strerror(errno));
}
}

Quat ned_frd_to_enu_flu(const Quat& q) {
    const Quat world{0, std::sqrt(0.5), std::sqrt(0.5), 0}, body{0, 1, 0, 0};
    auto result = normalized(product(product(world, normalized(q)), body));
    if (result[0] < 0) for (auto& value : result) value = -value;
    return result;
}
Vec3 geodetic_to_enu(const Vec3& p, const Vec3& origin) {
    validate(p);
    validate(origin);
    auto delta = ecef(p);
    const auto base = ecef(origin);
    for (std::size_t i=0; i<3; ++i) delta[i] -= base[i];
    const double lat = origin[0]*pi/180, lon = origin[1]*pi/180;
    const auto [x, y, z] = delta;
    return {-std::sin(lon)*x + std::cos(lon)*y,
            -std::sin(lat)*std::cos(lon)*x - std::sin(lat)*std::sin(lon)*y + std::cos(lat)*z,
            std::cos(lat)*std::cos(lon)*x + std::cos(lat)*std::sin(lon)*y + std::sin(lat)*z};
}
double monotonic_seconds() {
    timespec value{};
    if (::clock_gettime(CLOCK_MONOTONIC, &value) != 0) throw system_error("clock_gettime");
    return static_cast<double>(value.tv_sec) + static_cast<double>(value.tv_nsec)*1e-9;
}
Tracker::Tracker(Vec3 origin, std::uint8_t system_id) : origin_(origin), system_id_(system_id) {
    validate(origin);
    if (system_id == 0) throw std::invalid_argument("MAVLink system id must be 1..255");
}
bool Tracker::update(const mavlink_message_t& message, double received_at) {
    if (message.msgid != MAVLINK_MSG_ID_HIL_STATE_QUATERNION || message.sysid != system_id_ ||
        !std::isfinite(received_at)) return false;
    mavlink_hil_state_quaternion_t state{};
    mavlink_msg_hil_state_quaternion_decode(&message, &state);
    try {
        const Vec3 geodetic{state.lat*1e-7, state.lon*1e-7, state.alt*1e-3};
        const auto position = geodetic_to_enu(geodetic, origin_);
        const auto orientation = ned_frd_to_enu_flu({state.attitude_quaternion[0], state.attitude_quaternion[1],
                                                   state.attitude_quaternion[2], state.attitude_quaternion[3]});
        std::copy(position.begin(), position.end(), pose_.position);
        std::copy(orientation.begin(), orientation.end(), pose_.orientation);
        pose_.received_at = received_at;
        ++pose_.count;
        return true;
    } catch (const std::invalid_argument&) {
        return false;
    }
}

struct Receiver::Impl {
    int socket = -1;
    std::uint16_t bound_port = 0;
    Tracker tracker;
    std::mutex mutex;
    std::atomic<bool> stop{false};
    std::thread worker;
    std::string error;
    explicit Impl(Vec3 origin, std::uint8_t id) : tracker(origin, id) {}
    ~Impl() {
        stop = true;
        if (worker.joinable()) worker.join();
        if (socket >= 0) ::close(socket);
    }
    void receive() noexcept {
        try {
            std::array<std::uint8_t, 65536> bytes{};
            while (!stop) {
                pollfd descriptor{socket, POLLIN, 0};
                const int available = ::poll(&descriptor, 1, 100);
                if (available < 0) { if (errno == EINTR) continue; throw system_error("poll"); }
                if (available == 0) continue;
                if (descriptor.revents & (POLLERR|POLLHUP|POLLNVAL))
                    throw std::runtime_error("MAVLink UDP socket closed or failed");
                const auto size = ::recv(socket, bytes.data(), bytes.size(), 0);
                if (size < 0) {
                    if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
                    throw system_error("recv");
                }
                mavlink_message_t buffer{}, message{};
                mavlink_status_t parser{}, status{};
                for (ssize_t i=0; i<size && !stop; ++i) {
                    if (mavlink_frame_char_buffer(&buffer, &parser, bytes[static_cast<std::size_t>(i)],
                                                  &message, &status) == MAVLINK_FRAMING_OK) {
                        const double now = monotonic_seconds();
                        std::lock_guard<std::mutex> lock(mutex);
                        tracker.update(message, now);
                    }
                }
            }
        } catch (const std::exception& exception) {
            std::lock_guard<std::mutex> lock(mutex);
            error = exception.what();
        }
    }
};

Receiver::Receiver(const char* address, std::uint16_t port, Vec3 origin, std::uint8_t id)
    : impl_(std::make_unique<Impl>(origin, id)) {
    sockaddr_in local{};
    local.sin_family = AF_INET;
    local.sin_port = htons(port);
    if (!address || ::inet_pton(AF_INET, address, &local.sin_addr) != 1)
        throw std::invalid_argument("SIH receiver requires an IPv4 bind address");
    impl_->socket = ::socket(AF_INET, SOCK_DGRAM|SOCK_CLOEXEC|SOCK_NONBLOCK, 0);
    if (impl_->socket < 0) throw system_error("socket");
    if (::bind(impl_->socket, reinterpret_cast<sockaddr*>(&local), sizeof(local)) != 0)
        throw system_error("bind SIH UDP port");
    socklen_t length = sizeof(local);
    if (::getsockname(impl_->socket, reinterpret_cast<sockaddr*>(&local), &length) != 0)
        throw system_error("getsockname");
    impl_->bound_port = ntohs(local.sin_port);
    impl_->worker = std::thread([this] { impl_->receive(); });
}
Receiver::~Receiver() = default;
bool Receiver::snapshot(SihPose& pose) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if (!impl_->error.empty()) throw std::runtime_error(impl_->error);
    pose = impl_->tracker.pose();
    return pose.count != 0;
}
std::uint16_t Receiver::port() const { return impl_->bound_port; }
}

namespace {
void write_error(char* error, std::size_t size, const char* message) noexcept {
    if (error && size) std::snprintf(error, size, "%s", message);
}
}
extern "C" void* sih_open(const char* address, std::uint16_t port, std::uint8_t system_id,
                           const double* origin, char* error, std::size_t size) noexcept {
    try {
        if (!origin) throw std::invalid_argument("Origin is required");
        return new xovium::sih::Receiver(address, port, {origin[0], origin[1], origin[2]}, system_id);
    } catch (const std::exception& exception) { write_error(error, size, exception.what()); return nullptr; }
    catch (...) { write_error(error, size, "Unknown SIH receiver failure"); return nullptr; }
}
extern "C" int sih_snapshot(void* receiver, SihPose* pose, char* error, std::size_t size) noexcept {
    try {
        if (!receiver || !pose) throw std::invalid_argument("Closed/invalid SIH receiver");
        return static_cast<xovium::sih::Receiver*>(receiver)->snapshot(*pose) ? 1 : 0;
    } catch (const std::exception& exception) { write_error(error, size, exception.what()); return -1; }
    catch (...) { write_error(error, size, "Unknown SIH snapshot failure"); return -1; }
}
extern "C" void sih_close(void* receiver) noexcept {
    delete static_cast<xovium::sih::Receiver*>(receiver);
}
