#pragma once

#include <algorithm>
#include <cmath>

namespace ue_px4 {
constexpr double gravity = 9.80665;
constexpr double mass_kg = 2.0;
constexpr double pi = 3.14159265358979323846;
struct Vec3 { double x=0, y=0, z=0; };
struct Quat { double w=1, x=0, y=0, z=0; };
struct Controls { double roll=0, pitch=0, yaw=0, throttle=0; bool armed=false; };
struct State {
    Vec3 position_ned_m, velocity_ned_mps, acceleration_ned_mps2;
    Vec3 angular_velocity_frd_radps;
    Quat attitude_frd_to_ned;
};
struct Wrench { Vec3 force_frd_n, torque_frd_nm; };
inline Vec3 operator+(Vec3 a, Vec3 b) { return {a.x+b.x,a.y+b.y,a.z+b.z}; }
inline Vec3 operator-(Vec3 a, Vec3 b) { return {a.x-b.x,a.y-b.y,a.z-b.z}; }
inline Vec3 operator*(Vec3 v,double s) { return {v.x*s,v.y*s,v.z*s}; }
inline double norm(Vec3 v) { return std::sqrt(v.x*v.x+v.y*v.y+v.z*v.z); }
inline bool finite(Vec3 v) { return std::isfinite(v.x)&&std::isfinite(v.y)&&std::isfinite(v.z); }
inline bool valid(const State& s) {
    const auto q=s.attitude_frd_to_ned;
    const double n=q.w*q.w+q.x*q.x+q.y*q.y+q.z*q.z;
    return finite(s.position_ned_m)&&finite(s.velocity_ned_mps)&&finite(s.acceleration_ned_mps2)
        &&finite(s.angular_velocity_frd_radps)&&std::isfinite(n)&&std::abs(n-1)<1e-3;
}
inline Vec3 rotate(Quat q, Vec3 v) {
    const Vec3 t{2*(q.y*v.z-q.z*v.y),2*(q.z*v.x-q.x*v.z),2*(q.x*v.y-q.y*v.x)};
    return v+t*q.w+Vec3{q.y*t.z-q.z*t.y,q.z*t.x-q.x*t.z,q.x*t.y-q.y*t.x};
}
inline Vec3 inverse_rotate(Quat q,Vec3 v) { return rotate({q.w,-q.x,-q.y,-q.z},v); }
inline Vec3 ue_position_to_ned(Vec3 cm) { return {cm.x*.01,cm.y*.01,-cm.z*.01}; }
inline Vec3 ue_axial_to_frd(Vec3 v) { return {-v.x,-v.y,v.z}; }
inline Quat ue_quaternion_to_ned(Quat q) { return {q.w,-q.x,-q.y,q.z}; }

inline Controls filter_controls(Controls current,Controls target,double dt) {
    if (!target.armed) return {};
    auto servo=[dt](double a,double b,double tau,double rate,double lo) {
        const double step=(std::clamp(b,lo,1.0)-a)*(-std::expm1(-dt/tau));
        return a+std::clamp(step,-rate*dt,rate*dt);
    };
    return {servo(current.roll,target.roll,.04,8,-1),servo(current.pitch,target.pitch,.04,8,-1),
        servo(current.yaw,target.yaw,.04,8,-1),servo(current.throttle,target.throttle,.08,4,0),true};
}

inline Wrench aerodynamic_wrench(Vec3 v,Vec3 rates,Controls input) {
    const Controls c=input.armed ? input : Controls{};
    const double speed=norm(v), longitudinal=std::hypot(v.x,v.z);
    const double alpha=longitudinal>1e-8 ? std::atan2(v.z,v.x) : 0;
    const double beta=speed>1e-8 ? std::asin(std::clamp(v.y/speed,-1.0,1.0)) : 0;
    double blend=std::clamp((std::abs(alpha)-15*pi/180)/(15*pi/180),0.0,1.0);
    blend=blend*blend*(3-2*blend);
    const double cl=(1-blend)*std::clamp(.25+4.8*alpha,-1.4,1.4)+blend*1.1*std::sin(2*alpha);
    const double cd=.035+.065*cl*cl+blend*1.1*std::sin(alpha)*std::sin(alpha)+.3*(1-std::cos(alpha));
    const double qs=.5*1.168*speed*speed*.55;
    Vec3 force=speed>1e-8 ? v*(-qs*cd/speed) : Vec3{};
    if (longitudinal>1e-8) force=force+Vec3{v.z,0,-v.x}*(qs*cl/longitudinal);
    force.y+=qs*(-.8*beta+.12*std::clamp(c.yaw,-1.0,1.0));
    force.x+=22*std::clamp(c.throttle,0.0,1.0)*std::max(0.0,1-std::max(0.0,v.x)/40);
    const double span=1.8,chord=.31, inv2v=1/(2*std::max(speed,3.0));
    Vec3 moment{
        qs*span*(-.08*beta-.50*rates.x*span*inv2v+.16*std::clamp(c.roll,-1.0,1.0)),
        qs*chord*(.02-.65*alpha-7*rates.y*chord*inv2v+.65*std::clamp(c.pitch,-1.0,1.0)),
        qs*span*(.12*beta-.25*rates.z*span*inv2v+.10*std::clamp(c.yaw,-1.0,1.0))};
    return {force,moment};
}

struct SensorSample {
    Vec3 specific_force_frd, magnetic_field_frd;
    double latitude_deg,longitude_deg,altitude_m,pressure_hpa,temperature_c,differential_pressure_hpa;
    double indicated_airspeed_mps,true_airspeed_mps;
};
inline SensorSample sample_sensors(const State& s) {
    constexpr double lat=47.397742,lon=8.545594,alt=489.4,radius=6378137;
    const double height=alt-s.position_ned_m.z;
    const double temp=288.15-.0065*std::clamp(height,-1000.0,11000.0);
    double pressure=101325*std::pow(temp/288.15,gravity/(287.05*.0065));
    if (height>11000) pressure*=std::exp(-gravity*(height-11000)/(287.05*temp));
    const double density=pressure/(287.05*temp);
    const Vec3 body_v=inverse_rotate(s.attitude_frd_to_ned,s.velocity_ned_mps);
    const double airspeed=std::max(0.0,body_v.x);
    return {inverse_rotate(s.attitude_frd_to_ned,s.acceleration_ned_mps2-Vec3{0,0,gravity}),
        inverse_rotate(s.attitude_frd_to_ned,{.215,.011,.427}),
        lat+s.position_ned_m.x/radius*180/pi,
        lon+s.position_ned_m.y/(radius*std::cos(lat*pi/180))*180/pi,
        height,pressure/100,temp-273.15,.5*density*airspeed*airspeed/100,
        airspeed*std::sqrt(density/1.225),airspeed};
}
}
