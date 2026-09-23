#include "ue_px4/mavlink_link.hpp"
#include <common/mavlink.h>
#include <arpa/inet.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <limits>
#include <vector>

namespace ue_px4 {
namespace {
using Clock=std::chrono::steady_clock;
int wait_fd(int fd,short events,Clock::time_point deadline) {
    for (;;) {
        const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count();
        pollfd descriptor{fd,events,0};
        const int result=::poll(&descriptor,1,static_cast<int>(std::clamp<std::int64_t>(ms,0,2147483647)));
        if (result<0&&errno==EINTR) continue;
        return result;
    }
}
Clock::time_point deadline_after(double seconds) {
    return Clock::now()+std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
}
template<class T> T integer(double v) {
    return static_cast<T>(std::clamp(std::round(v),double(std::numeric_limits<T>::lowest()),double(std::numeric_limits<T>::max())));
}
}
struct MavlinkLink::Impl {
    int listener=-1,client=-1,bound_port=0;
    mavlink_status_t tx{},rx{};
    mavlink_message_t parser{};
    std::uint64_t last_sensor=0,last_gps=0,last_truth=0,last_heartbeat=0,last_control=0;
    bool send_bytes(const std::vector<std::uint8_t>& bytes,std::string& error) {
        std::size_t offset=0;
        const auto deadline=deadline_after(5);
        while (offset<bytes.size()) {
            if (wait_fd(client,POLLOUT,deadline)<=0) { error="PX4 sensor send timed out"; return false; }
            const auto count=::send(client,bytes.data()+offset,bytes.size()-offset,MSG_NOSIGNAL|MSG_DONTWAIT);
            if (count<0&&(errno==EINTR||errno==EAGAIN||errno==EWOULDBLOCK)) continue;
            if (count<=0) { error="PX4 sensor send failed: "+std::string(std::strerror(errno)); return false; }
            offset+=static_cast<std::size_t>(count);
        }
        return true;
    }
};
MavlinkLink::MavlinkLink():impl_(new Impl) {}
MavlinkLink::~MavlinkLink() { close(); }
void MavlinkLink::close() {
    if (impl_->client>=0) ::close(impl_->client);
    if (impl_->listener>=0) ::close(impl_->listener);
    *impl_=Impl{};
}
int MavlinkLink::port() const { return impl_->bound_port; }
bool MavlinkLink::listen(int port_number,std::string& error) {
    close(); error.clear();
    if (port_number<0||port_number>65535) { error="Invalid TCP port"; return false; }
    auto& p=*impl_;
    p.listener=::socket(AF_INET,SOCK_STREAM|SOCK_CLOEXEC|SOCK_NONBLOCK,0);
    const int enabled=1;
    sockaddr_in address{};
    address.sin_family=AF_INET; address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    address.sin_port=htons(static_cast<std::uint16_t>(port_number));
    if (p.listener<0||::setsockopt(p.listener,SOL_SOCKET,SO_REUSEADDR,&enabled,sizeof(enabled))<0
        ||::bind(p.listener,reinterpret_cast<sockaddr*>(&address),sizeof(address))<0||::listen(p.listener,1)<0) {
        error="Cannot listen for PX4 on loopback TCP: "+std::string(std::strerror(errno)); close(); return false;
    }
    socklen_t size=sizeof(address);
    if (::getsockname(p.listener,reinterpret_cast<sockaddr*>(&address),&size)<0) {
        error="Cannot read simulator port"; close(); return false;
    }
    p.bound_port=ntohs(address.sin_port); return true;
}
bool MavlinkLink::accept_client(double timeout,std::string& error) {
    error.clear(); auto& p=*impl_;
    if (!std::isfinite(timeout)||timeout<0||p.listener<0||p.client>=0) { error="Invalid accept state/timeout"; return false; }
    if (wait_fd(p.listener,POLLIN,deadline_after(timeout))<=0) { error="Waiting for PX4 connection timed out"; return false; }
    p.client=::accept4(p.listener,nullptr,nullptr,SOCK_CLOEXEC|SOCK_NONBLOCK);
    const int enabled=1;
    if (p.client<0||::setsockopt(p.client,IPPROTO_TCP,TCP_NODELAY,&enabled,sizeof(enabled))<0) {
        error="Cannot accept PX4: "+std::string(std::strerror(errno)); return false;
    }
    return true;
}
bool MavlinkLink::send_state(const State& s,std::uint64_t time,std::string& error) {
    auto& p=*impl_; error.clear();
    if (p.client<0||time<=p.last_sensor||!valid(s)) { error="Invalid state, non-increasing time, or disconnected PX4"; return false; }
    const auto r=sample_sensors(s);
    mavlink_message_t msg{};
    std::vector<std::uint8_t> bytes;
    auto append=[&]() {
        std::array<std::uint8_t,MAVLINK_MAX_PACKET_LEN> packet{};
        const auto n=mavlink_msg_to_send_buffer(packet.data(),&msg);
        bytes.insert(bytes.end(),packet.begin(),packet.begin()+n);
    };
    if (!p.last_heartbeat||time-p.last_heartbeat>=1000000) {
        mavlink_msg_heartbeat_pack_status(1,200,&p.tx,&msg,MAV_TYPE_GENERIC,MAV_AUTOPILOT_INVALID,0,0,MAV_STATE_ACTIVE);
        append(); p.last_heartbeat=time;
    }
    const auto lat=integer<std::int32_t>(r.latitude_deg*1e7),lon=integer<std::int32_t>(r.longitude_deg*1e7),alt=integer<std::int32_t>(r.altitude_m*1000);
    const auto vn=integer<std::int16_t>(s.velocity_ned_mps.x*100),ve=integer<std::int16_t>(s.velocity_ned_mps.y*100),vd=integer<std::int16_t>(s.velocity_ned_mps.z*100);
    if (!p.last_gps||time-p.last_gps>=100000) {
        const double speed=std::hypot(s.velocity_ned_mps.x,s.velocity_ned_mps.y);
        const auto course=speed>.1 ? static_cast<std::uint16_t>((static_cast<int>(std::round(std::atan2(s.velocity_ned_mps.y,s.velocity_ned_mps.x)*18000/pi))+36000)%36000) : std::uint16_t(65535);
        mavlink_msg_hil_gps_pack_status(1,200,&p.tx,&msg,time,3,lat,lon,alt,30,50,integer<std::uint16_t>(speed*100),vn,ve,vd,course,16,0,0);
        append(); p.last_gps=time;
    }
    if (!p.last_truth||time-p.last_truth>=20000) {
        const auto q=s.attitude_frd_to_ned;
        const float quat[4]={float(q.w),float(q.x),float(q.y),float(q.z)};
        const auto rates=s.angular_velocity_frd_radps;
        const auto acceleration=inverse_rotate(q,s.acceleration_ned_mps2);
        mavlink_msg_hil_state_quaternion_pack_status(1,200,&p.tx,&msg,time,quat,rates.x,rates.y,rates.z,lat,lon,alt,vn,ve,vd,
            integer<std::uint16_t>(r.indicated_airspeed_mps*100),integer<std::uint16_t>(r.true_airspeed_mps*100),
            integer<std::int16_t>(acceleration.x/gravity*1000),integer<std::int16_t>(acceleration.y/gravity*1000),integer<std::int16_t>(acceleration.z/gravity*1000));
        append(); p.last_truth=time;
    }
    const auto a=r.specific_force_frd,m=r.magnetic_field_frd,g=s.angular_velocity_frd_radps;
    mavlink_msg_hil_sensor_pack_status(1,200,&p.tx,&msg,time,a.x,a.y,a.z,g.x,g.y,g.z,m.x,m.y,m.z,
        r.pressure_hpa,r.differential_pressure_hpa,r.altitude_m,r.temperature_c,0x1FFF,0);
    append();
    if (!p.send_bytes(bytes,error)) return false;
    p.last_sensor=time; return true;
}
MavlinkLink::ReceiveResult MavlinkLink::receive_controls(Controls& controls,double timeout,std::string& error) {
    error.clear(); auto& p=*impl_;
    if (!std::isfinite(timeout)||timeout<0) { error="Invalid control timeout"; return ReceiveResult::Error; }
    if (p.client<0) { error="PX4 is not connected"; return ReceiveResult::Disconnected; }
    const auto deadline=deadline_after(timeout);
    bool received=false;
    do {
        const int ready=wait_fd(p.client,POLLIN,received ? Clock::now() : deadline);
        if (ready==0) return received ? ReceiveResult::Controls : ReceiveResult::Timeout;
        if (ready<0) { error=std::strerror(errno); return ReceiveResult::Error; }
        std::array<std::uint8_t,4096> data{};
        const auto n=::recv(p.client,data.data(),data.size(),MSG_DONTWAIT);
        if (n<0&&(errno==EAGAIN||errno==EWOULDBLOCK||errno==EINTR)) continue;
        if (n<=0) { error="PX4 disconnected; restart both processes"; return ReceiveResult::Disconnected; }
        for (ssize_t i=0;i<n;++i) {
            mavlink_message_t msg{}; mavlink_status_t status{};
            if (mavlink_frame_char_buffer(&p.parser,&p.rx,data[i],&msg,&status)!=MAVLINK_FRAMING_OK
                ||msg.msgid!=MAVLINK_MSG_ID_HIL_ACTUATOR_CONTROLS) continue;
            mavlink_hil_actuator_controls_t actuator{};
            mavlink_msg_hil_actuator_controls_decode(&msg,&actuator);
            if (!actuator.time_usec||actuator.time_usec<p.last_sensor||actuator.time_usec<=p.last_control) continue;
            if (!(actuator.flags&1)) { error="PX4 build does not advertise lockstep; rebuild px4_sitl_default with lockstep enabled"; return ReceiveResult::Error; }
            for (int j=0;j<4;++j) if (!std::isfinite(actuator.controls[j])) { error="PX4 sent nonfinite actuator values"; return ReceiveResult::Error; }
            controls={}; controls.armed=(actuator.mode&MAV_MODE_FLAG_SAFETY_ARMED)!=0;
            if (controls.armed) controls={std::clamp(double(actuator.controls[0]),-1.0,1.0),std::clamp(double(actuator.controls[1]),-1.0,1.0),
                std::clamp(double(actuator.controls[2]),-1.0,1.0),std::clamp(double(actuator.controls[3]),0.0,1.0),true};
            p.last_control=actuator.time_usec; received=true;
        }
    } while (Clock::now()<deadline);
    return received ? ReceiveResult::Controls : ReceiveResult::Timeout;
}
}
