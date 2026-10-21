#include "rover_physics.hpp"
#include "terrain.hpp"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <stdexcept>
#include <utility>
#include <iostream>
#include <limits>
#include <iomanip>
#include <nlohmann/json.hpp>

namespace xovium::rover {
namespace {
constexpr double pi = 3.14159265358979323846;
double positive(double value, const char* name) {
    if (!std::isfinite(value) || value <= 0) throw std::invalid_argument(std::string(name) + " must be positive and finite");
    return value;
}
px4isaac::Quat euler(double roll, double pitch, double yaw) {
    return px4isaac::quaternion_product(px4isaac::quaternion_product(
        {std::cos(yaw/2), 0, 0, std::sin(yaw/2)},
        {std::cos(pitch/2), 0, std::sin(pitch/2), 0}),
        {std::cos(roll/2), std::sin(roll/2), 0, 0});
}
}

HeightField::HeightField(const std::string& path) : path_(path) { reload(); }
struct HeightField::Dynamic {
    px4::terrain::Config config;
    std::unique_ptr<px4::terrain::Streamer> streamer;
    std::map<px4::terrain::TileKey,px4::terrain::TileMesh> meshes;
    std::filesystem::path export_directory;
    std::string filename(const px4::terrain::TileKey& key) const {
        return std::to_string(key.zoom)+"-"+std::to_string(key.x)+"-"+std::to_string(key.y)+".ac";
    }
    void export_mesh(const px4::terrain::TileMesh& mesh) const {
        if(export_directory.empty()) return;
        const auto path=export_directory/filename(mesh.key);
        std::ofstream output(path.string()+".tmp");
        output << std::setprecision(10) << "AC3Db\nMATERIAL \"terrain\" rgb 1 1 1 amb 0.4 0.4 0.4 emis 0 0 0 spec 0 0 0 shi 0 trans 0\n"
            << "OBJECT world\nkids 1\nOBJECT poly\nname \"terrain\"\ntexture " << std::quoted(mesh.textureFile.string())
            << "\nnumvert " << mesh.points.size() << '\n';
        for(const auto& point:mesh.points) output << point[0] << ' ' << point[2] << ' ' << -point[1] << '\n';
        output << "numsurf " << mesh.indices.size()/3 << '\n';
        for(std::size_t i=0;i<mesh.indices.size();i+=3) {
            output << "SURF 0x30\nmat 0\nrefs 3\n";
            for(int j=0;j<3;++j) {const auto k=mesh.indices[i+j]; output << k << ' ' << mesh.textureCoordinates[k][0] << ' ' << mesh.textureCoordinates[k][1] << '\n';}
        }
        output << "kids 0\n"; output.close();
        if(!output) throw std::runtime_error("Cannot export FlightGear dynamic terrain mesh");
        std::filesystem::rename(path.string()+".tmp",path);
    }
    void export_manifest() const {
        if(export_directory.empty()) return;
        nlohmann::json models=nlohmann::json::array();
        for(const auto& [key,mesh]:meshes) {
            (void)mesh;
            models.push_back({{"key",filename(key)},{"path",(export_directory/filename(key)).string()}});
        }
        std::ofstream output(export_directory/"scene.json.tmp");
        output << nlohmann::json{{"schema",1},{"origin",{{"latitude",config.originLatitude},{"longitude",config.originLongitude},{"altitude",config.originAltitude}}},{"models",models}}.dump();
        output.close();
        if(!output) throw std::runtime_error("Cannot export FlightGear dynamic terrain manifest");
        std::filesystem::rename(export_directory/"scene.json.tmp",export_directory/"scene.json");
    }
    double sample(double east, double north) const {
        // Mesh triangles share the exact elevation and projection used by the
        // Isaac/Unreal terrain backends; no synthetic flat substitute is used.
        for (const auto& [key,mesh] : meshes) {
            (void)key;
            if(mesh.points.empty()) continue;
            const auto& first=mesh.points.front(); const auto& last=mesh.points.back();
            if(east<std::min(first[0],last[0])-1 || east>std::max(first[0],last[0])+1
                || north<std::min(first[1],last[1])-1 || north>std::max(first[1],last[1])+1) continue;
            for (std::size_t i=0;i+2<mesh.indices.size();i+=3) {
                const auto& a=mesh.points[mesh.indices[i]];
                const auto& b=mesh.points[mesh.indices[i+1]];
                const auto& c=mesh.points[mesh.indices[i+2]];
                const double determinant=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);
                if (std::abs(determinant)<1e-9) continue;
                const double u=((b[1]-c[1])*(east-c[0])+(c[0]-b[0])*(north-c[1]))/determinant;
                const double v=((c[1]-a[1])*(east-c[0])+(a[0]-c[0])*(north-c[1]))/determinant;
                if (u>=-1e-6 && v>=-1e-6 && u+v<=1+1e-6) return u*a[2]+v*b[2]+(1-u-v)*c[2];
            }
        }
        return std::numeric_limits<double>::quiet_NaN();
    }
};

void HeightField::stream(const std::string& path) {
    if (path.empty()) return;
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Cannot open dynamic terrain configuration: "+path);
    const auto doc=nlohmann::json::parse(input);
    if (doc.at("schema")!=1) throw std::invalid_argument("Unsupported dynamic terrain schema");
    dynamic_=std::make_shared<Dynamic>();
    auto& config=dynamic_->config;
    config.source="xyz";
    config.originLatitude=doc.at("origin").at("latitude");
    config.originLongitude=doc.at("origin").at("longitude");
    config.originAltitude=doc.at("origin").at("altitude");
    config.cacheDirectory=std::filesystem::path(path).parent_path()/doc.at("cache").get<std::string>();
    config.zoom=doc.value("zoom",16); config.radius=doc.value("radius",2);
    config.imageryUrl=doc.at("imagery_url"); config.elevationUrl=doc.at("elevation_url");
    config.imageryExtension=doc.value("imagery_extension","png");
    config.meshCells=doc.value("mesh_cells",32);
    config.validate();
    dynamic_->streamer=std::make_unique<px4::terrain::Streamer>(config);
}

void HeightField::export_tiles(const std::string& directory) {
    if(directory.empty()) return;
    if(!dynamic_) throw std::invalid_argument("Tile export requires dynamic terrain");
    dynamic_->export_directory=std::filesystem::absolute(directory);
    std::filesystem::create_directories(dynamic_->export_directory);
}

bool HeightField::ready(double east, double north) {
    if (!dynamic_) return true;
    const auto desired=px4::terrain::desiredTiles(east,north,dynamic_->config);
    dynamic_->streamer->request(desired);
    bool changed=false;
    for (auto& result:dynamic_->streamer->collect()) {
        if (!result.error.empty()) std::cerr << "Terrain tile: " << result.error << '\n';
        else { dynamic_->export_mesh(result.mesh); dynamic_->meshes[result.key]=std::move(result.mesh); changed=true; }
    }
    for (auto it=dynamic_->meshes.begin();it!=dynamic_->meshes.end();) {
        if (std::find(desired.begin(),desired.end(),it->first)==desired.end()) { it=dynamic_->meshes.erase(it); changed=true; }
        else ++it;
    }
    if(changed) dynamic_->export_manifest();
    // Keep a 2m collision margin ready before advancing the physics clock.
    for (double dx:{-2.0,0.0,2.0}) for(double dy:{-2.0,0.0,2.0})
        if (!std::isfinite(dynamic_->sample(east+dx,north+dy))) return false;
    return true;
}
void HeightField::reload() {
    if (path_.empty()) return; // Flat ground is an explicit default only.
    const auto modified = std::filesystem::last_write_time(path_);
    if (!heights_.empty() && modified == modified_) return;
    std::ifstream input(path_);
    if (!input) throw std::runtime_error("Cannot read shared height field: " + path_.string());
    const auto document = nlohmann::json::parse(input);
    if (document.at("schema") != 1) throw std::invalid_argument("Unsupported shared height field schema");
    const auto width = document.at("width").get<std::size_t>();
    const auto height = document.at("height").get<std::size_t>();
    if (width < 2 || height < 2 || width > 16384 || height > 16384)
        throw std::invalid_argument("Height field dimensions must be 2..16384");
    std::vector<double> heights;
    for(const auto& sample:document.at("heights"))
        heights.push_back(sample.is_null() ? std::numeric_limits<double>::quiet_NaN() : sample.get<double>());
    if (heights.size() != width * height) throw std::invalid_argument("Height field sample count does not match dimensions");
    auto origin = document.at("origin").get<std::array<double, 3>>();
    auto spacing = document.at("spacing").get<std::array<double, 2>>();
    for (double value : heights) if (std::isinf(value)) throw std::invalid_argument("Height field contains infinite heights");
    for (double value : origin) if (!std::isfinite(value)) throw std::invalid_argument("Height field origin must be finite");
    for (double value : spacing) positive(value, "Height field spacing");
    width_ = width; height_ = height; heights_ = std::move(heights);
    origin_ = origin; spacing_ = spacing; modified_ = modified;
}

double HeightField::elevation(double east, double north) const {
    if (dynamic_) {
        const double value=dynamic_->sample(east,north);
        if (!std::isfinite(value)) throw std::runtime_error("Dynamic terrain collision tile is not loaded");
        return value;
    }
    if (heights_.empty()) return 0;
    const double x = (east - origin_[0]) / spacing_[0], y = (north - origin_[1]) / spacing_[1];
    if (x < 0 || y < 0 || x > width_ - 1 || y > height_ - 1)
        throw std::out_of_range("Rover reached the shared height field boundary; extend the terrain before continuing");
    const auto ix = std::min(static_cast<std::size_t>(x), width_ - 2);
    const auto iy = std::min(static_cast<std::size_t>(y), height_ - 2);
    const double fx = x - ix, fy = y - iy;
    const auto sample = [&](std::size_t dx, std::size_t dy) { return heights_[(iy + dy)*width_ + ix + dx]; };
    for(std::size_t dx=0;dx<2;++dx) for(std::size_t dy=0;dy<2;++dy)
        if(!std::isfinite(sample(dx,dy))) throw std::out_of_range("Rover reached an unmapped collision surface in the shared world");
    return origin_[2] + (1-fy)*((1-fx)*sample(0,0) + fx*sample(1,0)) + fy*((1-fx)*sample(0,1) + fx*sample(1,1));
}

Parameters load_parameters(const std::string& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Cannot read rover configuration: " + path);
    const auto document = nlohmann::json::parse(input);
    if (document.value("vehicle", "") != "r1_rover") throw std::invalid_argument("Rover physics requires vehicle=r1_rover");
    Parameters result;
    result.mass = document.value("mass_kg", result.mass);
    const auto& rover = document.at("rover");
    result.track = rover.at("wheel_track_m");
    result.wheelbase = rover.at("wheel_base_m");
    result.clearance = rover.at("wheel_radius_m").get<double>()-rover.at("wheel_center_z_m").get<double>();
    result.max_speed = rover.at("maximum_speed_m_s");
    result.max_yaw_rate = rover.value("maximum_yaw_rate_rad_s", result.max_yaw_rate);
    result.motor_tau = rover.value("motor_time_constant_s", result.motor_tau);
    result.motor_rate = rover.value("motor_rate_per_s", result.motor_rate);
    result.max_acceleration = 4*rover.at("maximum_torque_nm").get<double>()/(rover.at("wheel_radius_m").get<double>()*result.mass);
    result.friction = rover.at("tire_friction");
    result.rolling_resistance = rover.value("rolling_resistance", result.rolling_resistance);
    result.yaw_inertia = document.at("inertia_kg_m2").at(2).get<double>();
    result.right_channel = document.at("actuator_channels").at("right").get<int>();
    result.left_channel = document.at("actuator_channels").at("left").get<int>();
    for (auto pair : {std::pair{result.mass,"mass"}, {result.track,"track"}, {result.wheelbase,"wheelbase"},
                     {result.clearance,"clearance"}, {result.max_speed,"speed"}, {result.motor_tau,"motor response"},
                     {result.max_yaw_rate,"yaw rate"}, {result.motor_rate,"motor rate"}, {result.max_acceleration,"acceleration"},
                     {result.friction,"friction"}, {result.yaw_inertia,"yaw inertia"}})
        positive(pair.first, pair.second);
    if (!std::isfinite(result.rolling_resistance) || result.rolling_resistance < 0 || result.rolling_resistance > 1)
        throw std::invalid_argument("rolling_resistance must be in 0..1");
    if (result.right_channel < 0 || result.right_channel > 15 || result.left_channel < 0 || result.left_channel > 15
        || result.right_channel == result.left_channel) throw std::invalid_argument("Rover actuator channels must be distinct in 0..15");
    return result;
}

px4isaac::Quat Dynamics::attitude(double east, double north, double yaw) const {
    const double fx = std::cos(yaw)*config_.wheelbase/2, fy = std::sin(yaw)*config_.wheelbase/2;
    const double lx = -std::sin(yaw)*config_.track/2, ly = std::cos(yaw)*config_.track/2;
    const double forward_slope = (terrain_.elevation(east+fx,north+fy) - terrain_.elevation(east-fx,north-fy))/config_.wheelbase;
    const double left_slope = (terrain_.elevation(east+lx,north+ly) - terrain_.elevation(east-lx,north-ly))/config_.track;
    return euler(std::atan(left_slope), -std::atan(forward_slope), yaw);
}

Dynamics::Dynamics(Parameters parameters, HeightField terrain, const px4isaac::Config& config)
    : config_(parameters), terrain_(std::move(terrain)), heading_((90-config.spawn_heading_deg)*pi/180) {
    state_.position_enu = config.spawn_position_enu_m;
    state_.position_enu[2] = terrain_.elevation(state_.position_enu[0], state_.position_enu[1]) + config_.clearance;
    state_.quaternion_wxyz = attitude(state_.position_enu[0],state_.position_enu[1],heading_);
}

void Dynamics::step(double right, double left, double dt, bool armed) {
    if (!std::isfinite(dt) || dt <= 0 || dt > 0.02 || !std::isfinite(right) || !std::isfinite(left))
        throw std::invalid_argument("Invalid rover control or simulation step");
    const auto previous = state_;
    right = armed ? std::clamp(right,-1.0,1.0) : 0;
    left = armed ? std::clamp(left,-1.0,1.0) : 0;
    const double mean = (right+left)/2;
    const double limit = std::min(1.0, config_.max_yaw_rate*config_.track/(2*config_.max_speed));
    const double differential = std::clamp((right-left)/2,-limit,limit);
    const std::array<double,2> targets{mean+differential,mean-differential};
    for (std::size_t i=0;i<2;++i) motors_[i] += std::clamp(
        (targets[i]-motors_[i])*(1-std::exp(-dt/config_.motor_tau)),
        -config_.motor_rate*dt,config_.motor_rate*dt);
    const auto gravity_body = px4isaac::inverse_rotate(state_.quaternion_wxyz,{0,0,-px4isaac::gravity});
    const double normal_force = config_.mass*std::max(0.0,-gravity_body[2]);
    const double force_limit = std::min(config_.mass*config_.max_acceleration/2,config_.friction*normal_force/2);
    const double right_speed = speed_ + yaw_rate_*config_.track/2;
    const double left_speed = speed_ - yaw_rate_*config_.track/2;
    const auto force = [&](double target,double current) { return std::clamp(config_.mass/2*(target*config_.max_speed-current)/config_.motor_tau,-force_limit,force_limit); };
    const double force_right = force(motors_[0],right_speed), force_left = force(motors_[1],left_speed);
    double acceleration = (force_right+force_left)/config_.mass + gravity_body[0];
    const double resistance = config_.rolling_resistance*normal_force/config_.mass;
    if (std::abs(speed_) > resistance*dt) acceleration -= std::copysign(resistance,speed_);
    else if (std::abs(acceleration) < resistance) { speed_=0; acceleration=0; }
    speed_ += acceleration*dt;
    const double yaw_acceleration = ((force_right-force_left)*config_.track/2 - 0.12*yaw_rate_)/config_.yaw_inertia;
    yaw_rate_ += yaw_acceleration*dt;
    heading_ = std::remainder(heading_+yaw_rate_*dt,2*pi);
    const auto forward = px4isaac::rotate(state_.quaternion_wxyz,{1,0,0});
    state_.position_enu[0] += speed_*forward[0]*dt;
    state_.position_enu[1] += speed_*forward[1]*dt;
    state_.position_enu[2] = terrain_.elevation(state_.position_enu[0],state_.position_enu[1]) + config_.clearance;
    state_.quaternion_wxyz = attitude(state_.position_enu[0],state_.position_enu[1],heading_);
    // Reject an impassable height discontinuity as a contact obstacle.
    if (std::abs(state_.position_enu[2]-previous.position_enu[2]) > std::abs(speed_)*dt*1.7 + 0.002) {
        state_.position_enu=previous.position_enu; state_.quaternion_wxyz=previous.quaternion_wxyz; speed_=0;
    }
    for (std::size_t i=0;i<3;++i) {
        state_.velocity_enu[i]=(state_.position_enu[i]-previous.position_enu[i])/dt;
        state_.acceleration_enu[i]=(state_.velocity_enu[i]-previous.velocity_enu[i])/dt;
    }
    const auto& p=previous.quaternion_wxyz;
    auto delta=px4isaac::quaternion_product({p[0],-p[1],-p[2],-p[3]},state_.quaternion_wxyz);
    if (delta[0]<0) for (double& value:delta) value=-value;
    const double sine=std::hypot(delta[1],delta[2],delta[3]);
    const double scale=sine>1e-12 ? 2*std::atan2(sine,delta[0])/(sine*dt) : 2/dt;
    for (std::size_t i=0;i<3;++i) state_.angular_velocity_flu[i]=delta[i+1]*scale;
}
}
