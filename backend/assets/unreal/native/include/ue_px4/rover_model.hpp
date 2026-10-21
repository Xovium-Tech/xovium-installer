#pragma once
#include "flight_model.hpp"

namespace ue_px4 {
constexpr double rover_mass_kg = 20.0;
constexpr double rover_track_m = 0.30;
constexpr double rover_wheelbase_m = 0.30;
constexpr double rover_suspension_length_m = 0.16;

// Four spring/damper contacts support the Chaos rigid body. Tire traction is
// bounded by the local normal load; airborne wheels never create propulsion.
struct TireForce { double longitudinal=0, lateral=0; };
inline double rover_normal_force(double distance, double normal_velocity) {
    if (distance < 0 || distance >= rover_suspension_length_m) return 0;
    return std::clamp(4500 * (rover_suspension_length_m-distance) - 180 * normal_velocity,
                      0.0, rover_mass_kg*gravity);
}
inline TireForce rover_tire_force(double normal, double forward_velocity,
                                  double lateral_velocity, double command, bool armed) {
    if (normal <= 0) return {};
    // A geared reversible drive approaches 2.1 m/s at full input. Zero input
    // applies motor braking, including disarm; contact still permits gravity.
    const double target = armed ? 2.1*std::clamp(command,-1.0,1.0) : 0;
    TireForce result{std::clamp(60*(target-forward_velocity),-25.0,25.0), -55*lateral_velocity};
    const double magnitude=std::hypot(result.longitudinal,result.lateral);
    const double limit=.9*normal;
    if (magnitude>limit) {
        result.longitudinal*=limit/magnitude;
        result.lateral*=limit/magnitude;
    }
    return result;
}
}
