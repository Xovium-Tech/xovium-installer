#include "rover_physics.hpp"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <nlohmann/json.hpp>

namespace {
volatile std::sig_atomic_t stopping=0;
void stop(int) { stopping=1; }
constexpr double pi=3.14159265358979323846;
class FlightGear {
    int socket_=-1;
    sockaddr_in destination_{};
public:
    explicit FlightGear(int port) {
        if (!port) return;
        socket_=::socket(AF_INET,SOCK_DGRAM|SOCK_CLOEXEC,0);
        if (socket_<0) throw std::runtime_error("Cannot create FlightGear visual socket");
        destination_.sin_family=AF_INET; destination_.sin_port=htons(port);
        destination_.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    }
    ~FlightGear() { if(socket_>=0) ::close(socket_); }
    void publish(const px4isaac::VehicleState& state,const px4isaac::Vec3& origin,double sim_time) {
        if(socket_<0) return;
        const auto location=px4isaac::enu_to_geodetic(state.position_enu,origin);
        const auto q=px4isaac::attitude_ned_frd(state.quaternion_wxyz);
        const double roll=std::atan2(2*(q[0]*q[1]+q[2]*q[3]),1-2*(q[1]*q[1]+q[2]*q[2]));
        const double pitch=std::asin(std::clamp(2*(q[0]*q[2]-q[3]*q[1]),-1.0,1.0));
        const double yaw=std::atan2(2*(q[0]*q[3]+q[1]*q[2]),1-2*(q[2]*q[2]+q[3]*q[3]));
        const auto velocity=px4isaac::enu_to_ned(state.velocity_enu);
        char output[1024];
        const int size=std::snprintf(output,sizeof(output),"%.10f,%.10f,%.6f,%.8f,%.8f,%.8f,%.6f,%.6f,%.6f,%.6f\n",
            location[0],location[1],location[2]/0.3048,roll*180/pi,pitch*180/pi,std::fmod(yaw*180/pi+360,360),
            velocity[0]/0.3048,velocity[1]/0.3048,velocity[2]/0.3048,sim_time);
        if(size<=0 || size>=static_cast<int>(sizeof(output))) throw std::runtime_error("Invalid FlightGear state packet");
        if(::sendto(socket_,output,size,0,reinterpret_cast<const sockaddr*>(&destination_),sizeof(destination_))<0)
            throw std::runtime_error("Cannot publish FlightGear rover state");
    }
};
}

int main(int argc,char** argv) try {
    std::string config_path,heightfield_path,terrain_path,state_path,export_path;
    px4isaac::Vec3 origin{47.397742,8.545594,489.4};
    int instance=0,fg_port=0,fg_position_port=0;
    bool terrain_only=false;
    double duration=0;
    for(int i=1;i<argc;++i) {
        const std::string option=argv[i];
        const auto next=[&]() -> std::string { if(++i>=argc) throw std::invalid_argument("Missing argument for "+option); return argv[i]; };
        if(option=="--config") config_path=next();
        else if(option=="--heightfield") heightfield_path=next();
        else if(option=="--terrain-config") terrain_path=next();
        else if(option=="--state-file") state_path=next();
        else if(option=="--terrain-export") export_path=next();
        else if(option=="--terrain-only") terrain_only=true;
        else if(option=="--fg-position-port") fg_position_port=std::stoi(next());
        else if(option=="--instance") instance=std::stoi(next());
        else if(option=="--fg-port") fg_port=std::stoi(next());
        else if(option=="--duration") duration=std::stod(next());
        else if(option=="--origin") for(double& value:origin) value=std::stod(next());
        else throw std::invalid_argument("Unknown option: "+option);
    }
    if(config_path.empty() && !terrain_only) throw std::invalid_argument("--config rover.json is required");
    if(instance<0 || instance>254 || fg_port<0 || fg_port>65535 || fg_position_port<0 || fg_position_port>65535 || !std::isfinite(duration) || duration<0)
        throw std::invalid_argument("Invalid instance, port, or duration");
    xovium::rover::HeightField terrain(heightfield_path);
    terrain.stream(terrain_path);
    terrain.export_tiles(export_path);
    std::signal(SIGINT,stop); std::signal(SIGTERM,stop);
    if(terrain_only) {
        if(terrain_path.empty() || export_path.empty() || !fg_position_port)
            throw std::invalid_argument("Terrain renderer requires --terrain-config, --terrain-export, --fg-position-port");
        const int fd=::socket(AF_INET,SOCK_DGRAM|SOCK_CLOEXEC|SOCK_NONBLOCK,0);
        if(fd<0) throw std::runtime_error("Cannot create terrain position socket");
        sockaddr_in address{}; address.sin_family=AF_INET; address.sin_port=htons(fg_position_port); address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        if(::bind(fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))<0) {::close(fd);throw std::runtime_error("Cannot bind terrain position socket");}
        double east=0,north=0;
        while(!stopping) {
            char data[256]{}; ssize_t count;
            while((count=::recv(fd,data,sizeof(data)-1,0))>0) {
                data[count]='\0'; double latitude,longitude;
                if(std::sscanf(data,"%lf,%lf",&latitude,&longitude)==2 && std::isfinite(latitude) && std::isfinite(longitude)) {
                    constexpr double earth=6378137;
                    east=(longitude-origin[1])*pi/180*earth*std::cos(origin[0]*pi/180);
                    north=(latitude-origin[0])*pi/180*earth;
                }
            }
            terrain.ready(east,north);
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        ::close(fd); return 0;
    }
    const auto config=px4isaac::load_config(config_path);
    const auto parameters=xovium::rover::load_parameters(config_path);
    const auto start=std::chrono::steady_clock::now();
    while(!stopping && !terrain.ready(config.spawn_position_enu_m[0],config.spawn_position_enu_m[1])) {
        if(std::chrono::steady_clock::now()-start>std::chrono::seconds(120))
            throw std::runtime_error("Dynamic terrain could not load within 120 seconds; verify URLs/network or populate the cache");
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    if(stopping) return 0;
    xovium::rover::Dynamics physics(parameters,std::move(terrain),config);
    px4isaac::MavlinkBridge bridge(px4isaac::SensorModel(config,origin),4560+instance,"127.0.0.1",0.5,false);
    FlightGear flightgear(fg_port);
    std::cout << "Rover differential-drive physics: TCP " << bridge.port() << "; actuator 0 right, 1 left; reversible motors\n" << std::flush;
    const auto running_since=std::chrono::steady_clock::now();
    auto next=running_since;
    std::uint64_t sim_us=0;
    bool waiting=false;
    while(!stopping) {
        const auto now=std::chrono::steady_clock::now();
        if(duration && std::chrono::duration<double>(now-running_since).count()>=duration) break;
        bridge.poll();
        if(!bridge.failure().empty()) throw std::runtime_error(bridge.failure());
        if(bridge.ready_to_step() && now>=next) {
            if(!physics.terrain_ready()) {
                if(!waiting) std::cerr << "Waiting for streamed collision terrain; physics clock paused\n";
                waiting=true;
            } else {
                waiting=false;
                physics.step(bridge.controls()[parameters.right_channel],bridge.controls()[parameters.left_channel],0.004,bridge.armed());
                sim_us+=4000;
                bridge.publish(physics.state(),sim_us);
                if(sim_us%20000==0) flightgear.publish(physics.state(),origin,sim_us/1e6);
                if(sim_us%1000000==0) physics.reload_terrain();
                if(!state_path.empty() && sim_us%100000==0) {
                    const auto& state=physics.state();
                    std::ofstream output(state_path+".tmp");
                    output << nlohmann::json{{"time_us",sim_us},{"position_enu",state.position_enu},{"velocity_enu",state.velocity_enu},
                        {"quaternion",state.quaternion_wxyz},{"armed",bridge.armed()},{"controls",bridge.controls()}}.dump() << '\n';
                    output.close(); std::filesystem::rename(state_path+".tmp",state_path);
                }
                next=std::max(next+std::chrono::milliseconds(4),now);
            }
        }
        std::this_thread::sleep_for(std::chrono::microseconds(500));
    }
    return 0;
} catch(const std::exception& error) {
    std::cerr << "Rover simulation: " << error.what() << '\n'; return 1;
}
