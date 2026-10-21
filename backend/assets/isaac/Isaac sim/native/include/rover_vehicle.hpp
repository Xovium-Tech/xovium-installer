#pragma once

#include "flight_core.hpp"
#include <omni/physx/IPhysx.h>
#include <pxr/usd/usdGeom/cylinder.h>
#include <pxr/usd/usdGeom/cube.h>
#include <pxr/usd/usdGeom/xform.h>
#include <pxr/usd/usdPhysics/collisionGroup.h>
#include <pxr/usd/usd/collectionAPI.h>
#include <algorithm>
#include <array>
#include <cmath>

namespace px4isaac {

// Native PhysX wheel, suspension and tire dynamics with independent electric
// motors. Tire forces come from ground queries and longitudinal/lateral slip.
class RoverVehicle {
    pxr::UsdStageWeakPtr stage_;
    Config config_;
    std::array<pxr::SdfPath,4> wheels_;
    template<class T> static void set(pxr::UsdPrim prim,const char* name,const pxr::SdfValueTypeName& type,const T& value) {
        prim.CreateAttribute(pxr::TfToken(name),type,false).Set(value);
    }
    static void api(pxr::UsdPrim prim,const char* name) { prim.AddAppliedSchema(pxr::TfToken(name)); }
public:
    RoverVehicle(pxr::UsdStageWeakPtr stage,pxr::UsdPrim body,pxr::UsdPrim scene,const Config& config)
        : stage_(stage),config_(config) {
        using namespace pxr;
        body.RemoveAppliedSchema(TfToken("PhysicsArticulationRootAPI"));
        body.RemoveAppliedSchema(TfToken("PhysxArticulationAPI"));
        api(scene,"PhysxVehicleContextAPI");
        set(scene,"physxVehicleContext:updateMode",SdfValueTypeNames->Token,TfToken("velocityChange"));
        set(scene,"physxVehicleContext:verticalAxis",SdfValueTypeNames->Token,TfToken("posZ"));
        set(scene,"physxVehicleContext:longitudinalAxis",SdfValueTypeNames->Token,TfToken("posX"));
        api(body,"PhysxVehicleAPI");
        set(body,"physxVehicle:vehicleEnabled",SdfValueTypeNames->Bool,true);
        // The native vehicle solver includes gravity in suspension/tire integration.
        set(body,"physxRigidBody:disableGravity",SdfValueTypeNames->Bool,true);
        set(body,"physxVehicle:lowForwardSpeedSubStepCount",SdfValueTypeNames->Int,3);
        set(body,"physxVehicle:highForwardSpeedSubStepCount",SdfValueTypeNames->Int,1);
        set(body,"physxVehicle:subStepThresholdLongitudinalSpeed",SdfValueTypeNames->Float,3.f);
        set(body,"physxVehicle:minPassiveLongitudinalSlipDenominator",SdfValueTypeNames->Float,.5f);
        set(body,"physxVehicle:minActiveLongitudinalSlipDenominator",SdfValueTypeNames->Float,.1f);
        set(body,"physxVehicle:minLateralSlipDenominator",SdfValueTypeNames->Float,.2f);
        body.SetCustomDataByKey(TfToken("physxVehicle:referenceFrameIsCenterOfMass"),VtValue(false));

        const SdfPath root("/World/IsaacRoverVehicle");
        UsdGeomXform::Define(stage,root);
        const auto query_path=root.AppendChild(TfToken("GroundQueries"));
        const auto chassis_path=root.AppendChild(TfToken("ChassisCollision"));
        auto query=UsdPhysicsCollisionGroup::Define(stage,query_path);
        auto chassis=UsdPhysicsCollisionGroup::Define(stage,chassis_path);
        query.CreateFilteredGroupsRel().SetTargets({chassis_path});
        chassis.CreateFilteredGroupsRel().SetTargets({query_path});
        UsdCollectionAPI(chassis.GetPrim(),TfToken("colliders")).CreateIncludesRel().SetTargets({body.GetPath()});
        const auto friction_path=root.AppendChild(TfToken("TireFriction"));
        auto friction=stage->DefinePrim(friction_path,TfToken("PhysxVehicleTireFrictionTable"));
        set(friction,"defaultFrictionValue",SdfValueTypeNames->Float,float(config.rover.tire_friction));

        const double sprung_mass=config.mass_kg/4;
        const double rest_load=sprung_mass*gravity;
        const double travel=.04;
        const double spring=rest_load/(travel/2);
        const double damping=1.4*std::sqrt(spring*sprung_mass);
        for (std::size_t i=0;i<4;++i) {
            wheels_[i]=body.GetPath().AppendChild(TfToken("DrivenWheel"+std::to_string(i)));
            const GfVec3f local((i%2==0?1:-1)*config.rover.wheel_base_m/2,
                (i<2?-1:1)*config.rover.wheel_track_m/2,config.rover.wheel_center_z_m);
            auto wheel=UsdGeomXform::Define(stage,wheels_[i]);
            wheel.AddTranslateOp(UsdGeomXformOp::PrecisionFloat).Set(local);
            auto prim=wheel.GetPrim();
            for (auto schema : {"PhysxVehicleWheelAttachmentAPI","PhysxVehicleWheelAPI","PhysxVehicleTireAPI",
                               "PhysxVehicleSuspensionAPI","PhysxVehicleWheelControllerAPI"}) api(prim,schema);
            prim.CreateRelationship(TfToken("physxVehicleWheelAttachment:collisionGroup")).SetTargets({query_path});
            prim.CreateRelationship(TfToken("physxVehicleTire:frictionTable")).SetTargets({friction_path});
            set(prim,"physxVehicleWheelAttachment:index",SdfValueTypeNames->Int,int(i));
            set(prim,"physxVehicleWheelAttachment:suspensionTravelDirection",SdfValueTypeNames->Float3,GfVec3f(0,0,-1));
            set(prim,"physxVehicleWheelAttachment:suspensionFramePosition",SdfValueTypeNames->Float3,local+GfVec3f(0,0,travel/2));
            set(prim,"physxVehicleWheel:radius",SdfValueTypeNames->Float,float(config.rover.wheel_radius_m));
            set(prim,"physxVehicleWheel:width",SdfValueTypeNames->Float,float(config.rover.wheel_width_m));
            set(prim,"physxVehicleWheel:mass",SdfValueTypeNames->Float,float(config.rover.wheel_mass_kg));
            set(prim,"physxVehicleWheel:moi",SdfValueTypeNames->Float,float(config.rover.wheel_mass_kg*config.rover.wheel_radius_m*config.rover.wheel_radius_m/2));
            set(prim,"physxVehicleWheel:dampingRate",SdfValueTypeNames->Float,.005f);
            // Skid steering intentionally permits lateral tire slip. Longitudinal
            // grip remains strong enough for propulsion and braking.
            set(prim,"physxVehicleTire:lateralStiffnessGraph",SdfValueTypeNames->Float2,GfVec2f(2.f,2.f*rest_load));
            set(prim,"physxVehicleTire:longitudinalStiffness",SdfValueTypeNames->Float,1000.f);
            set(prim,"physxVehicleTire:camberStiffness",SdfValueTypeNames->Float,0.f);
            set(prim,"physxVehicleTire:frictionVsSlipGraph",SdfValueTypeNames->Float2Array,
                VtArray<GfVec2f>{GfVec2f(0,1),GfVec2f(.1,1),GfVec2f(1,1)});
            set(prim,"physxVehicleSuspension:springStrength",SdfValueTypeNames->Float,float(spring));
            set(prim,"physxVehicleSuspension:springDamperRate",SdfValueTypeNames->Float,float(damping));
            set(prim,"physxVehicleSuspension:travelDistance",SdfValueTypeNames->Float,float(travel));
            set(prim,"physxVehicleWheelController:driveTorque",SdfValueTypeNames->Float,0.f);
            set(prim,"physxVehicleWheelController:brakeTorque",SdfValueTypeNames->Float,float(config.rover.maximum_torque_nm));
            set(prim,"physxVehicleWheelController:steerAngle",SdfValueTypeNames->Float,0.f);
            auto tire=UsdGeomCylinder::Define(stage,wheels_[i].AppendChild(TfToken("Tire")));
            tire.CreateAxisAttr().Set(UsdGeomTokens->y);
            tire.CreateRadiusAttr().Set(config.rover.wheel_radius_m);
            tire.CreateHeightAttr().Set(config.rover.wheel_width_m);
            tire.CreateDisplayColorAttr().Set(VtArray<GfVec3f>{GfVec3f(.055f)});
            auto marker=UsdGeomCube::Define(stage,wheels_[i].AppendChild(TfToken("Tread")));
            marker.CreateSizeAttr().Set(1.0);
            marker.AddScaleOp().Set(GfVec3f(.012f,config.rover.wheel_width_m+.001,config.rover.wheel_radius_m*1.8));
            marker.CreateDisplayColorAttr().Set(VtArray<GfVec3f>{GfVec3f(.3f)});
        }
    }
    void drive(omni::physx::IPhysx* physics,const std::array<double,2>& target_rad_s,bool armed) {
        using namespace pxr;
        std::array<omni::physx::usdparser::ObjectId,4> ids;
        std::array<omni::physx::VehicleWheelState,4> states{};
        for (std::size_t i=0;i<4;++i) ids[i]=physics->getObjectId(wheels_[i],omni::physx::ePTVehicleWheelAttachment);
        if (!physics->getWheelState(ids.data(),ids.size(),states.data())) throw std::runtime_error("Native PhysX rover wheel state unavailable");
        for (std::size_t i=0;i<4;++i) {
            auto prim=stage_->GetPrimAtPath(wheels_[i]);
            const double target=armed ? target_rad_s[i<2?0:1] : 0;
            const bool brake=std::abs(target)<.01;
            const double torque=brake ? 0 : std::clamp(.3*(target-states[i].rotationSpeed),
                -config_.rover.maximum_torque_nm,config_.rover.maximum_torque_nm);
            set(prim,"physxVehicleWheelController:driveTorque",SdfValueTypeNames->Float,float(torque));
            set(prim,"physxVehicleWheelController:brakeTorque",SdfValueTypeNames->Float,float(brake?config_.rover.maximum_torque_nm:0));
        }
    }
};

}
