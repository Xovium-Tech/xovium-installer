#include <omni/usd/UsdContextIncludes.h>
#include <omni/usd/UsdContext.h>
#include <carb/PluginUtils.h>
#include <carb/events/EventsUtils.h>
#include <carb/settings/ISettings.h>
#include <omni/ext/IExt.h>
#include <omni/kit/IApp.h>
#include <omni/timeline/ITimeline.h>
#include <omni/physx/IPhysx.h>
#include <omni/physx/IPhysxSimulation.h>
#include <physicsSchemaTools/UsdTools.h>
#include <pxr/usd/usdPhysics/scene.h>
#include <pxr/usd/usdPhysics/rigidBodyAPI.h>
#include <pxr/usd/usdPhysics/massAPI.h>
#include <pxr/usd/usdPhysics/collisionAPI.h>
#include <pxr/usd/usdGeom/metrics.h>
#include <pxr/usd/usd/primRange.h>
#include <pxr/usd/usd/editContext.h>
#include <pxr/usd/usdGeom/xformable.h>
#include <pxr/usd/usdUtils/stageCache.h>
#include "flight_core.hpp"
#include "IFlight.hpp"
#include <chrono>
#include <memory>
#include <stdexcept>
#include <cstdio>
#include <fstream>
#include <cmath>
#include <filesystem>
#include <dlfcn.h>
#include <thread>

using namespace pxr;
using namespace px4isaac;
namespace {
using Clock = std::chrono::steady_clock;
carb::settings::ISettings* settings = nullptr;
std::string setting(const char* key, const char* fallback = "") {
    const char* value = settings->getStringBuffer(key);
    return value && *value ? value : fallback;
}
carb::Float3 float3(const Vec3& v) { return {float(v[0]),float(v[1]),float(v[2])}; }
GfVec3d vec3(const Vec3& v) { return {v[0],v[1],v[2]}; }
template<class T> void attr(UsdPrim prim, const char* name, const SdfValueTypeName& type, const T& value) {
    prim.CreateAttribute(TfToken(name),type,false).Set(value);
}
class FlightExtension;
FlightExtension* instance = nullptr;
class FlightExtension : public omni::ext::IExt {
    carb::events::ISubscriptionPtr update_;
    omni::kit::IApp* app_ = nullptr;
    omni::physx::IPhysx* physx_ = nullptr;
    omni::physx::IPhysxSimulation* simulation_ = nullptr;
    UsdStageWeakPtr stage_;
    SdfPath path_;
    long stage_id_ = 0;
    std::uint64_t body_id_ = 0;
    Config config_;
    Vec3 origin_{};
    std::unique_ptr<Airframe> airframe_;
    std::unique_ptr<MavlinkBridge> bridge_;
    VehicleState state_;
    Vec3 angular_enu_{};
    bool initialized_ = false, failed_ = false, had_connection_ = false;
    bool physics_check_ = false, check_passed_ = false;
    unsigned check_step_ = 0;
    unsigned settle_ = 0;
    std::uint64_t steps_ = 0;
    double physics_time_ = 0, maximum_height_ = 0;
    Clock::time_point next_step_, started_, next_status_;
    const double dt_ = 0.004;
    void fail(const std::string& message) {
        if (failed_) return;
        failed_ = true;
        std::fprintf(stderr, "PX4 Isaac native: %s\n",message.c_str());
        if (bridge_) bridge_->close();
        app_->postUncancellableQuit(1);
    }
    void initialize() {
        auto* context = omni::usd::UsdContext::getContext();
        stage_ = context->getStage();
        if (!stage_) return;
        UsdEditContext edits(stage_, stage_->GetSessionLayer());
        if (UsdGeomGetStageUpAxis(stage_) != UsdGeomTokens->z || std::abs(UsdGeomGetStageMetersPerUnit(stage_)-1.0)>1e-9)
            throw std::runtime_error("World must use metres and Z up.");
        if (!stage_->GetCompositionErrors().empty()) throw std::runtime_error("World contains unresolved asset references.");
        path_ = SdfPath(setting("/px4/flight/modelPath","/World/Aircraft"));
        auto body = stage_->GetPrimAtPath(path_);
        if (!body || !body.HasAPI<UsdPhysicsRigidBodyAPI>()) throw std::runtime_error("Model path must identify a USD rigid body.");
        bool collider = false;
        for (auto prim : UsdPrimRange(body)) if (prim.HasAPI<UsdPhysicsCollisionAPI>()) collider = true;
        if (!collider) throw std::runtime_error("Aircraft requires collision geometry.");
        auto transform = UsdGeomXformable(body);
        transform.ClearXformOpOrder();
        transform.AddTranslateOp().Set(vec3(config_.spawn_position_enu_m));
        auto orientation = heading_quaternion(config_.spawn_heading_deg);
        transform.AddOrientOp(UsdGeomXformOp::PrecisionDouble).Set(GfQuatd(orientation[0],orientation[1],orientation[2],orientation[3]));
        auto mass = UsdPhysicsMassAPI::Apply(body);
        mass.CreateMassAttr().Set(float(config_.mass_kg));
        mass.CreateDiagonalInertiaAttr().Set(GfVec3f(config_.inertia_kg_m2[0],config_.inertia_kg_m2[1],config_.inertia_kg_m2[2]));
        mass.CreateCenterOfMassAttr().Set(GfVec3f(0));
        mass.CreatePrincipalAxesAttr().Set(GfQuatf(1));
        auto rigid = UsdPhysicsRigidBodyAPI(body);
        rigid.CreateVelocityAttr().Set(GfVec3f(0));
        rigid.CreateAngularVelocityAttr().Set(GfVec3f(0));
        rigid.CreateKinematicEnabledAttr().Set(false);
        rigid.CreateRigidBodyEnabledAttr().Set(true);
        body.AddAppliedSchema(TfToken("PhysxRigidBodyAPI"));
        attr(body,"physxRigidBody:linearDamping",SdfValueTypeNames->Float,0.f);
        attr(body,"physxRigidBody:angularDamping",SdfValueTypeNames->Float,0.f);
        attr(body,"physxRigidBody:sleepThreshold",SdfValueTypeNames->Float,0.f);
        attr(body,"physxRigidBody:enableCCD",SdfValueTypeNames->Bool,true);
        attr(body,"physxRigidBody:solverPositionIterationCount",SdfValueTypeNames->Int,16);
        attr(body,"physxRigidBody:solverVelocityIterationCount",SdfValueTypeNames->Int,4);
        UsdPhysicsScene scene;
        for (auto prim : stage_->Traverse()) if (prim.IsA<UsdPhysicsScene>()) {
            if (scene) throw std::runtime_error("Use one physics scene in the world.");
            scene = UsdPhysicsScene(prim);
        }
        if (!scene) scene = UsdPhysicsScene::Define(stage_,SdfPath("/World/PhysicsScene"));
        scene.CreateGravityDirectionAttr().Set(GfVec3f(0,0,-1));
        scene.CreateGravityMagnitudeAttr().Set(float(gravity));
        auto scene_prim = scene.GetPrim();
        scene_prim.AddAppliedSchema(TfToken("PhysxSceneAPI"));
        attr(scene_prim,"physxScene:enableGPUDynamics",SdfValueTypeNames->Bool,false);
        attr(scene_prim,"physxScene:broadphaseType",SdfValueTypeNames->Token,TfToken("MBP"));
        attr(scene_prim,"physxScene:solverType",SdfValueTypeNames->Token,TfToken("TGS"));
        attr(scene_prim,"physxScene:enableCCD",SdfValueTypeNames->Bool,true);
        attr(scene_prim,"physxScene:timeStepsPerSecond",SdfValueTypeNames->UInt,250u);
        auto timeline = omni::timeline::getTimeline();
        timeline->stop();
        timeline->setAutoUpdate(false);
        stage_id_ = context->getStageId();
        if (!simulation_->attachStage(stage_id_)) throw std::runtime_error("PhysX could not attach the world.");
        body_id_ = sdfPathToInt(path_);
        omni::physx::ISimulationCallback callback;
        callback.velocityWriteFn = [this](uint64_t path, const carb::Float3& linear, const carb::Float3& angular, void*) {
            if (path != body_id_) return;
            state_.velocity_enu = {linear.x,linear.y,linear.z};
            angular_enu_ = {angular.x,angular.y,angular.z};
        };
        simulation_->setSimulationCallback(callback);
        simulation_->addSimulationOutputFlags(omni::physx::SimulationOutputType::eVELOCITY,
            omni::physx::SimulationOutputFlag::eNOTIFY_UPDATE | omni::physx::SimulationOutputFlag::eNOTIFY_IN_RADIANS,&body_id_,1);
        physx_->setSimulationLayer(stage_->GetSessionLayer()->GetIdentifier().c_str());
        state_.position_enu = config_.spawn_position_enu_m;
        state_.quaternion_wxyz = orientation;
        initialized_ = true;
        next_step_ = started_ = Clock::now();
        next_status_ = started_ + std::chrono::seconds(5);
        std::printf("Native PhysX ready: model %s; PX4 TCP %u; 250 Hz lockstep.\n",path_.GetText(),bridge_->port());
        std::fflush(stdout);
    }
    void read_state(const Vec3& previous_velocity) {
        carb::Float3 position{};
        carb::Float4 orientation{};
        if (!physx_->getRigidBodyTransformation(path_,position,orientation)) throw std::runtime_error("Aircraft rigid body was removed or disabled.");
        state_.position_enu = {position.x,position.y,position.z};
        state_.quaternion_wxyz = {orientation.w,orientation.x,orientation.y,orientation.z};
        state_.angular_velocity_flu = inverse_rotate(state_.quaternion_wxyz,angular_enu_);
        for (int i=0;i<3;++i) state_.acceleration_enu[i] = (state_.velocity_enu[i]-previous_velocity[i])/dt_;
        for (auto value: state_.position_enu) if (!std::isfinite(value)) throw std::runtime_error("PhysX produced a non-finite state.");
        maximum_height_ = std::max(maximum_height_,state_.position_enu[2]);
    }
    void report() {
        auto filename = setting("/px4/flight/report");
        if (filename.empty()) return;
        std::ofstream output(filename);
        output << "{\"native\":true,\"steps\":" << steps_ << ",\"simulation_seconds\":" << steps_*dt_
               << ",\"maximum_altitude_m\":" << maximum_height_ << ",\"altitude_m\":" << state_.position_enu[2]
               << ",\"physics_check_passed\":" << (check_passed_?"true":"false")
               << ",\"failed\":" << (failed_?"true":"false") << "}\n";
    }
    void teleport(const Vec3& position, const Vec3& velocity) {
        UsdEditContext edits(stage_,stage_->GetSessionLayer());
        auto body = stage_->GetPrimAtPath(path_);
        body.GetAttribute(TfToken("xformOp:translate")).Set(vec3(position));
        auto q = heading_quaternion(0);
        body.GetAttribute(TfToken("xformOp:orient")).Set(GfQuatd(q[0],q[1],q[2],q[3]));
        UsdPhysicsRigidBodyAPI(body).GetVelocityAttr().Set(GfVec3f(velocity[0],velocity[1],velocity[2]));
        UsdPhysicsRigidBodyAPI(body).GetAngularVelocityAttr().Set(GfVec3f(0));
        simulation_->flushChanges();
        state_.velocity_enu = velocity;
        angular_enu_ = {};
    }
    void check_before_step() {
        if (check_step_ == 0) teleport({0,0,3},{0,0,0});
        if (check_step_ == 400) teleport({18,30,3},{0,10,0});
        if (check_step_ == 650) {
            UsdEditContext edits(stage_,stage_->GetSessionLayer());
            auto fuselage = stage_->GetPrimAtPath(path_.AppendChild(TfToken("Fuselage")));
            if (!fuselage) throw std::runtime_error("Physics check requires the supplied airplane model.");
            fuselage.SetActive(false);
            simulation_->flushChanges();
        }
    }
    void check_after_step() {
        ++check_step_;
        if (check_step_ == 25 && !(state_.position_enu[2] > 2.92 && state_.position_enu[2] < 2.98 &&
            state_.velocity_enu[2] > -1.02 && state_.velocity_enu[2] < -0.94 && std::abs(state_.acceleration_enu[2]+gravity)<0.1))
            throw std::runtime_error("Native gravity check failed.");
        if (check_step_ == 400 && !(state_.position_enu[2] > 0.2 && state_.position_enu[2] < 0.4 && std::abs(state_.velocity_enu[2])<0.1))
            throw std::runtime_error("Native ground collision check failed.");
        if (check_step_ == 650 && !(state_.position_enu[1] < 36 && state_.position_enu[1]>32))
            throw std::runtime_error("Native obstacle collision check failed.");
        if (check_step_ == 675) {
            check_passed_ = true;
            std::printf("Native physics check passed: gravity, ground, obstacle, live collider edit.\n");
            report();
            app_->postUncancellableQuit(0);
        }
    }
    void update() {
        try {
            if (failed_) return;
            if (!initialized_) { initialize(); return; }
            if (omni::usd::UsdContext::getContext()->getStage() != stage_) throw std::runtime_error("World changed; restart Isaac and PX4 together.");
            if (auto timeline = omni::timeline::getTimeline(); timeline && timeline->isPlaying()) {
                timeline->pause();
                timeline->setAutoUpdate(false);
                throw std::runtime_error("The timeline changed during PX4 simulation. Restart Isaac and PX4; the native flight plugin advances physics automatically.");
            }
            if (!settings->getAsBool("/px4/flight/externalTick")) {
                auto deadline = Clock::now()+std::chrono::milliseconds(8);
                do { tick(); std::this_thread::sleep_for(std::chrono::microseconds(150)); }
                while (!failed_ && Clock::now()<deadline);
            }
            physx_->updateTransformations(false,true,false,false);
            UsdEditContext edits(stage_,stage_->GetSessionLayer());
            auto camera = stage_->GetPrimAtPath(SdfPath("/World/ChaseCamera"));
            if (camera) {
                auto offset = rotate(state_.quaternion_wxyz,{-8,0,3});
                auto eye = vec3(state_.position_enu)+vec3(offset);
                auto look = vec3(state_.position_enu)+GfVec3d(0,0,0.3);
                GfMatrix4d matrix;
                matrix.SetLookAt(eye,look,GfVec3d(0,0,1));
                auto xform = UsdGeomXformable(camera);
                xform.MakeMatrixXform().Set(matrix.GetInverse());
            }
            double duration = settings->getAsFloat64("/px4/flight/duration");
            if (duration > 0 && std::chrono::duration<double>(Clock::now()-started_).count() >= duration) {
                if (physics_check_ && !check_passed_) throw std::runtime_error("Physics check did not finish before the duration limit.");
                report();
                app_->postUncancellableQuit(0);
            }
        } catch (const std::exception& error) { fail(error.what()); }
    }
public:
    void onStartup(const char*) override {
        instance = this;
        app_ = carb::getFramework()->acquireInterface<omni::kit::IApp>();
        settings = carb::getFramework()->acquireInterface<carb::settings::ISettings>();
        try {
            Dl_info location{};
            if (!dladdr(reinterpret_cast<void*>(&setting),&location) || !location.dli_fname)
                throw std::runtime_error("Cannot locate native extension assets.");
            auto project = std::filesystem::path(location.dli_fname).parent_path().parent_path().parent_path().parent_path();
            auto config_file = setting("/px4/flight/config");
            if (config_file.empty()) config_file = (project/"aircraft.json").string();
            config_ = load_config(config_file);
            physics_check_ = settings->getAsBool("/px4/flight/physicsCheck");
            origin_ = {settings->getAsFloat64("/px4/flight/latitude"),settings->getAsFloat64("/px4/flight/longitude"),settings->getAsFloat64("/px4/flight/altitude")};
            airframe_ = std::make_unique<Airframe>(config_);
            bridge_ = std::make_unique<MavlinkBridge>(SensorModel(config_,origin_),4560+settings->getAsInt("/px4/flight/instance"));
            physx_ = carb::getFramework()->tryAcquireInterface<omni::physx::IPhysx>();
            simulation_ = carb::getFramework()->tryAcquireInterface<omni::physx::IPhysxSimulation>();
            if (!physx_ || !simulation_) throw std::runtime_error("Installed PhysX interfaces are incompatible with the compiled native extension.");
            auto world = setting("/px4/flight/world");
            auto* context = omni::usd::UsdContext::getContext();
            if (world.empty() && !context->getStage()) world = (project/"worlds/runway.usda").string();
            if (!world.empty() && !context->openStage(world.c_str())) throw std::runtime_error("Cannot open world: "+world);
            update_ = carb::events::createSubscriptionToPop(app_->getUpdateEventStream(),[this](carb::events::IEvent*) { update(); },0,"px4-native-flight");
        } catch (const std::exception& error) { fail(error.what()); }
    }
    void tick() {
        if (!initialized_ || failed_) return;
        try {
            bridge_->poll();
            if (!bridge_->failure().empty()) throw std::runtime_error(bridge_->failure());
            if (Clock::now() < next_step_) return;
            if (settings->getAsBool("/px4/terrain/enabled") && settings->getAsBool("/px4/terrain/active") &&
                !settings->getAsBool("/px4/terrain/ready")) { next_step_ = Clock::now(); return; }
            if (settle_ >= 250 && !physics_check_ && (!bridge_->connected() || !bridge_->ready_to_step())) { next_step_ = Clock::now(); return; }
            if (!stage_->GetPrimAtPath(path_)) throw std::runtime_error("Aircraft was deleted; restart Isaac and PX4 together.");
            if (simulation_->getAttachedStage() != stage_id_) throw std::runtime_error("Physics world was stopped or replaced; restart Isaac and PX4 together.");
            simulation_->flushChanges();
            if (settle_ >= 250 && physics_check_) check_before_step();
            if (settle_ >= 250 && !physics_check_) {
                if (!had_connection_) { had_connection_ = true; std::printf("PX4 connected; sending native HIL sensors.\n"); }
                Controls targets{};
                if (bridge_->armed()) for (int i=0;i<4;++i) targets[i] = bridge_->controls()[config_.actuator_channels[i]];
                else airframe_->reset_controls();
                auto control = airframe_->filter_controls(targets,dt_);
                auto velocity = state_.velocity_enu;
                for(int i=0;i<3;++i) velocity[i] -= config_.wind_enu_m_s[i];
                auto force = airframe_->evaluate(inverse_rotate(state_.quaternion_wxyz,velocity),state_.angular_velocity_flu,
                    atmosphere(origin_[2]+state_.position_enu[2]).density_kg_m3,control);
                simulation_->addForceAtPos(stage_id_,body_id_,float3(rotate(state_.quaternion_wxyz,force.force_flu)),float3(state_.position_enu),omni::physx::ForceModeType::eFORCE);
                simulation_->addTorque(stage_id_,body_id_,float3(rotate(state_.quaternion_wxyz,force.torque_flu)));
            }
            auto previous_velocity = state_.velocity_enu;
            simulation_->simulate(float(dt_),float(physics_time_));
            simulation_->fetchResults();
            physics_time_ += dt_;
            read_state(previous_velocity);
            if (settle_ < 250) ++settle_;
            else if (physics_check_) { ++steps_; check_after_step(); }
            else bridge_->publish(state_,++steps_*4000);
            next_step_ += std::chrono::milliseconds(4);
            if (next_step_ < Clock::now()-std::chrono::milliseconds(100)) next_step_ = Clock::now();
            if (Clock::now() >= next_status_) {
                std::printf("Native simulation %.2f s; altitude %.2f m; %s\n",steps_*dt_,state_.position_enu[2],bridge_->armed()?"armed":"disarmed");
                std::fflush(stdout);
                next_status_ = Clock::now()+std::chrono::seconds(5);
                report();
            }
        } catch (const std::exception& error) { fail(error.what()); }
    }
    void onShutdown() override {
        update_.reset();
        report();
        if (bridge_) bridge_->close();
        if (initialized_ && simulation_) simulation_->detachStage();
        initialized_ = false;
        instance = nullptr;
        stage_.Reset();
    }
};
}
const carb::PluginImplDesc descriptor{"px4.isaac.flight.plugin","Native PX4 fixed wing","Local",carb::PluginHotReload::eDisabled,"1.0"};
CARB_PLUGIN_IMPL_DEPS(omni::kit::IApp,carb::settings::ISettings)
CARB_PLUGIN_IMPL(descriptor,FlightExtension,px4isaac::IFlight)
void fillInterface(FlightExtension&) {}
void fillInterface(px4isaac::IFlight& iface) { iface.tick = [] { if(instance) instance->tick(); }; }
