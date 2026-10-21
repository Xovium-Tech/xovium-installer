#define CARB_EXPORTS

#include "terrain.hpp"

#include <carb/PluginUtils.h>
#include <carb/eventdispatcher/IEventDispatcher.h>
#include <carb/logging/Log.h>
#include <carb/settings/ISettings.h>
#include <omni/ext/IExt.h>
#include <omni/kit/IApp.h>
#include <omni/physx/IPhysxSimulation.h>
#include <omni/usd/UsdContextIncludes.h>
#include <omni/usd/UsdContext.h>
#include <pxr/usd/sdf/copyUtils.h>
#include <pxr/usd/usd/editContext.h>
#include <pxr/usd/usdGeom/mesh.h>
#include <pxr/usd/usdGeom/metrics.h>
#include <pxr/usd/usdGeom/primvarsAPI.h>
#include <pxr/usd/usdGeom/xform.h>
#include <pxr/usd/usdGeom/xformCache.h>
#include <pxr/usd/usdPhysics/collisionAPI.h>
#include <pxr/usd/usdPhysics/materialAPI.h>
#include <pxr/usd/usdPhysics/meshCollisionAPI.h>
#include <pxr/usd/usdPhysics/rigidBodyAPI.h>
#include <pxr/usd/usdShade/materialBindingAPI.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <map>
#include <memory>
#include <stdexcept>

PXR_NAMESPACE_USING_DIRECTIVE

namespace {
const SdfPath rootPath("/World/StreamedTerrain");

std::string setting(carb::settings::ISettings* settings, const char* name) {
    const char* result = settings->getStringBuffer(name);
    return result ? result : "";
}

std::string component(int value) { return value < 0 ? "n" + std::to_string(-value) : "p" + std::to_string(value); }

SdfPath tilePath(const px4::terrain::TileKey& key) {
    return rootPath.AppendChild(TfToken("Tile_" + std::to_string(key.zoom) + "_" + component(key.x) + "_" + component(key.y)));
}

px4::terrain::Config readConfig(carb::settings::ISettings* settings, const UsdStageWeakPtr& stage) {
    px4::terrain::Config config;
    config.source = setting(settings, "/px4/terrain/source");
    config.cacheDirectory = setting(settings, "/px4/terrain/cacheDirectory");
    if (config.cacheDirectory.empty()) {
        const auto file = std::filesystem::path(stage->GetRootLayer()->GetRealPath());
        config.cacheDirectory = file.empty() ? std::filesystem::current_path() / "cache/terrain" :
            file.parent_path().parent_path() / "cache/terrain";
    }
    config.cacheDirectory = std::filesystem::absolute(config.cacheDirectory);
    config.imageryUrl = setting(settings, "/px4/terrain/imageryUrl");
    config.elevationUrl = setting(settings, "/px4/terrain/elevationUrl");
    config.imageryExtension = setting(settings, "/px4/terrain/imageryExtension");
    config.originLatitude = settings->getAsFloat64("/px4/terrain/originLatitude");
    config.originLongitude = settings->getAsFloat64("/px4/terrain/originLongitude");
    config.originAltitude = settings->getAsFloat64("/px4/terrain/originAltitude");
    config.tileSize = settings->getAsFloat64("/px4/terrain/tileSize");
    config.syntheticHeight = settings->getAsFloat64("/px4/terrain/syntheticHeight");
    config.seed = settings->getAsInt("/px4/terrain/seed");
    config.zoom = settings->getAsInt("/px4/terrain/zoom");
    config.radius = settings->getAsInt("/px4/terrain/radius");
    config.meshCells = settings->getAsInt("/px4/terrain/meshCells");
    config.collision = settings->getAsBool("/px4/terrain/collision");
    config.validate();
    return config;
}

void authorTile(const UsdStageWeakPtr& stage, const px4::terrain::TileMesh& data, bool collision) {
    const auto path = tilePath(data.key);
    UsdGeomMesh mesh = UsdGeomMesh::Define(stage, path);
    VtVec3fArray points;
    VtVec2fArray textureCoordinates;
    points.reserve(data.points.size());
    textureCoordinates.reserve(data.textureCoordinates.size());
    for (const auto& point : data.points) points.emplace_back(point[0], point[1], point[2]);
    for (const auto& coordinate : data.textureCoordinates) textureCoordinates.emplace_back(coordinate[0], coordinate[1]);
    mesh.CreatePointsAttr().Set(points);
    mesh.CreateFaceVertexCountsAttr().Set(VtIntArray(data.indices.size() / 3, 3));
    mesh.CreateFaceVertexIndicesAttr().Set(VtIntArray(data.indices.begin(), data.indices.end()));
    mesh.CreateSubdivisionSchemeAttr().Set(UsdGeomTokens->none);
    mesh.CreateDoubleSidedAttr().Set(false);
    UsdGeomPrimvarsAPI(mesh).CreatePrimvar(TfToken("st"), SdfValueTypeNames->TexCoord2fArray,
                                         UsdGeomTokens->vertex).Set(textureCoordinates);
    UsdShadeMaterial material = UsdShadeMaterial::Define(stage, path.AppendChild(TfToken("Material")));
    UsdShadeShader surface = UsdShadeShader::Define(stage, material.GetPath().AppendChild(TfToken("Surface")));
    surface.CreateIdAttr().Set(TfToken("UsdPreviewSurface"));
    surface.CreateInput(TfToken("roughness"), SdfValueTypeNames->Float).Set(1.0f);
    surface.CreateInput(TfToken("metallic"), SdfValueTypeNames->Float).Set(0.0f);
    UsdShadeShader coordinates = UsdShadeShader::Define(stage, material.GetPath().AppendChild(TfToken("Coordinates")));
    coordinates.CreateIdAttr().Set(TfToken("UsdPrimvarReader_float2"));
    coordinates.CreateInput(TfToken("varname"), SdfValueTypeNames->Token).Set(TfToken("st"));
    UsdShadeShader texture = UsdShadeShader::Define(stage, material.GetPath().AppendChild(TfToken("Texture")));
    texture.CreateIdAttr().Set(TfToken("UsdUVTexture"));
    texture.CreateInput(TfToken("file"), SdfValueTypeNames->Asset).Set(SdfAssetPath(data.textureFile.string()));
    texture.CreateInput(TfToken("sourceColorSpace"), SdfValueTypeNames->Token).Set(TfToken("sRGB"));
    texture.CreateInput(TfToken("wrapS"), SdfValueTypeNames->Token).Set(TfToken("clamp"));
    texture.CreateInput(TfToken("wrapT"), SdfValueTypeNames->Token).Set(TfToken("clamp"));
    texture.CreateInput(TfToken("st"), SdfValueTypeNames->Float2).ConnectToSource(coordinates.ConnectableAPI(), TfToken("result"));
    surface.CreateInput(TfToken("diffuseColor"), SdfValueTypeNames->Color3f).ConnectToSource(texture.ConnectableAPI(), TfToken("rgb"));
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), TfToken("surface"));
    UsdShadeMaterialBindingAPI::Apply(mesh.GetPrim()).Bind(material);
    if (collision) {
        UsdPhysicsCollisionAPI::Apply(mesh.GetPrim()).CreateCollisionEnabledAttr().Set(true);
        UsdPhysicsMeshCollisionAPI::Apply(mesh.GetPrim()).CreateApproximationAttr().Set(TfToken("none"));
        const auto physicsMaterial = UsdPhysicsMaterialAPI::Apply(material.GetPrim());
        physicsMaterial.CreateStaticFrictionAttr().Set(0.65f);
        physicsMaterial.CreateDynamicFrictionAttr().Set(0.6f);
        physicsMaterial.CreateRestitutionAttr().Set(0.02f);
        UsdShadeMaterialBindingAPI(mesh).Bind(material, UsdShadeTokens->weakerThanDescendants, TfToken("physics"));
    }
}

}

class TerrainExtension final : public omni::ext::IExt {
public:
    void onStartup(const char*) override {
        settings_ = carb::getCachedInterface<carb::settings::ISettings>();
        settings_->setBool("/px4/terrain/active", true);
        resetMetrics();
        settings_->setString("/px4/terrain/status", "Waiting for aircraft stage");
        auto* dispatcher = carb::getCachedInterface<carb::eventdispatcher::IEventDispatcher>();
        observer_ = dispatcher->observeEvent(carb::RStringKey("px4.isaac.terrain.update"), 10,
            omni::kit::kGlobalEventUpdate, [this](const carb::eventdispatcher::Event&) {
                try { update(); }
                catch (const std::exception& error) {
                    resetMetrics();
                    settings_->setString("/px4/terrain/status", error.what());
                    if (lastError_ != error.what()) { CARB_LOG_ERROR("Terrain: %s", error.what()); lastError_ = error.what(); }
                }
            });
    }

    void onShutdown() override {
        observer_.reset();
        reset();
        if (settings_) {
            settings_->setBool("/px4/terrain/active", false);
            settings_->setBool("/px4/terrain/ready", false);
            settings_->setString("/px4/terrain/status", "Disabled");
        }
    }

private:
    void resetMetrics() {
        if (!settings_) return;
        settings_->setBool("/px4/terrain/ready", false);
        settings_->setBool("/px4/terrain/viewReady", false);
        for (const auto* name : {"requiredTiles", "coveredTiles", "pendingTiles", "loadedTiles"})
            settings_->setInt((std::string("/px4/terrain/") + name).c_str(), 0);
        settings_->setFloat64("/px4/terrain/authorMilliseconds", 0.0);
    }

    void reset() {
        streamer_.reset();
        if (stage_) {
            UsdEditContext edit(stage_, stage_->GetSessionLayer());
            stage_->RemovePrim(rootPath);
            const auto ground = stage_->GetPrimAtPath(SdfPath("/World/Ground"));
            if (ground && groundDisabled_) ground.SetActive(groundWasActive_);
        }
        stage_.Reset();
        loaded_.clear();
        pending_.clear();
        havePosition_ = false;
        groundDisabled_ = false;
        resetMetrics();
    }

    // USD can queue collider creation until the next simulation step. Publish
    // coverage only after PhysX has consumed the tile's additions/removals.
    void flushCollisionChanges() {
        if (!config_.collision) return;
        auto* simulation = carb::getCachedInterface<omni::physx::IPhysxSimulation>();
        if (!simulation) throw std::runtime_error("Terrain collision requires PhysX");
        simulation->flushChanges();
    }

    void update() {
        if (!settings_->getAsBool("/px4/terrain/enabled")) {
            if (stage_) reset();
            settings_->setBool("/px4/terrain/ready", false);
            settings_->setString("/px4/terrain/status", "Disabled");
            return;
        }
        auto* context = omni::usd::UsdContext::getContext();
        const UsdStageWeakPtr current = context ? context->getStage() : UsdStageWeakPtr();
        if (current != stage_) {
            reset();
            settings_->setBool("/px4/terrain/ready", false);
            if (!current) return;
            if (UsdGeomGetStageUpAxis(current) != UsdGeomTokens->z || std::abs(UsdGeomGetStageMetersPerUnit(current) - 1) > 1e-9)
                throw std::runtime_error("Terrain world must use metres and Z up");
            if (current->GetPrimAtPath(rootPath)) throw std::runtime_error("/World/StreamedTerrain is reserved for the terrain extension");
            config_ = readConfig(settings_, current);
            streamer_ = std::make_unique<px4::terrain::Streamer>(config_);
            stage_ = current;
            lastError_.clear();
            const auto ground = stage_->GetPrimAtPath(SdfPath("/World/Ground"));
            groundWasActive_ = ground && ground.IsActive();
            UsdEditContext edit(stage_, stage_->GetSessionLayer());
            UsdGeomXform::Define(stage_, rootPath);
            CARB_LOG_INFO("Terrain source: %s", config_.source == "procedural" ? "offline procedural (synthetic)" : "configured XYZ imagery and Terrarium elevation");
        }
        if (!stage_ || !streamer_) return;
        const auto aircraft = stage_->GetPrimAtPath(SdfPath(setting(settings_, "/px4/terrain/targetPath")));
        if (!aircraft) { resetMetrics(); return; }
        UsdGeomXformCache transforms;
        const auto position = transforms.GetLocalToWorldTransform(aircraft).ExtractTranslation();
        const auto now = std::chrono::steady_clock::now();
        GfVec3f velocity(0);
        UsdPhysicsRigidBodyAPI(aircraft).GetVelocityAttr().Get(&velocity);
        double speed = std::hypot(velocity[0], velocity[1]);
        if (!std::isfinite(speed)) speed = 0;
        if (havePosition_) {
            const double elapsed = std::chrono::duration<double>(now - previousTime_).count();
            const double distance = std::hypot(position[0] - previousPosition_[0], position[1] - previousPosition_[1]);
            // Teleports need local coverage, not a kilometre-wide swept footprint.
            if (elapsed > 0 && distance <= std::max(10.0, elapsed * 1000.0))
                speed = std::max(speed, distance / elapsed);
        }
        previousPosition_ = position;
        previousTime_ = now;
        havePosition_ = true;
        const double margin = std::max(8.0, 4.0 + speed * 0.25);
        const auto required = config_.collision ?
            px4::terrain::requiredTiles(position[0], position[1], margin, config_) :
            std::vector<px4::terrain::TileKey>{};
        const auto desired = px4::terrain::desiredTiles(position[0], position[1], config_);
        // A slow outer download must not displace the ground needed by the next
        // physics steps. The streamer honours this priority for queued work.
        std::vector<px4::terrain::TileKey> requested = required;
        std::set<px4::terrain::TileKey> wanted(required.begin(), required.end());
        for (const auto& key : desired) if (wanted.insert(key).second) requested.push_back(key);
        streamer_->request(requested);
        for (auto iterator = pending_.begin(); iterator != pending_.end();)
            if (!wanted.count(iterator->first)) iterator = pending_.erase(iterator);
            else ++iterator;
        for (auto& result : streamer_->collect()) {
            if (!result.error.empty()) {
                CARB_LOG_WARN("Terrain tile %d/%d/%d: %s", result.key.zoom, result.key.x, result.key.y, result.error.c_str());
            } else if (!loaded_.count(result.key)) pending_.insert_or_assign(result.key, std::move(result.mesh));
        }

        // Building an entire ring in one live-stage update caused visible frame
        // stalls. Assemble one tile privately, then expose its complete subtree
        // with one USD copy; at most one tile is added or removed per update.
        const auto authorStart = std::chrono::steady_clock::now();
        bool changed = false;
        for (const auto& key : requested) {
            const auto iterator = pending_.find(key);
            if (iterator == pending_.end()) continue;
            const auto detached = UsdStage::CreateInMemory();
            authorTile(detached, iterator->second, config_.collision);
            if (!SdfCopySpec(detached->GetRootLayer(), tilePath(key), stage_->GetSessionLayer(), tilePath(key)))
                throw std::runtime_error("Cannot publish terrain tile to the active USD stage");
            flushCollisionChanges();
            loaded_.insert(key);
            pending_.erase(iterator);
            changed = true;
            break;
        }
        const auto covered = std::count_if(required.begin(), required.end(),
            [this](const auto& key) { return loaded_.count(key) != 0; });
        const bool ready = !config_.collision || (!required.empty() && covered == required.size());
        const bool viewReady = std::all_of(desired.begin(), desired.end(),
            [this](const auto& key) { return loaded_.count(key) != 0; });
        UsdEditContext edit(stage_, stage_->GetSessionLayer());
        if (ready && !changed) {
            // One extra ring prevents remove/recreate churn at a tile boundary.
            // Retain the old ground until replacement collision is usable.
            std::set<px4::terrain::TileKey> retained;
            for (const auto& key : requested)
                for (int x = -1; x <= 1; ++x) for (int y = -1; y <= 1; ++y)
                    retained.insert({key.x + x, key.y + y, key.zoom});
            for (auto iterator = loaded_.begin(); iterator != loaded_.end(); ++iterator) {
                if (retained.count(*iterator)) continue;
                stage_->RemovePrim(tilePath(*iterator));
                flushCollisionChanges();
                loaded_.erase(iterator);
                changed = true;
                break;
            }
        }
        if (ready && config_.collision && !groundDisabled_) {
            const auto ground = stage_->GetPrimAtPath(SdfPath("/World/Ground"));
            if (ground) { ground.SetActive(false); flushCollisionChanges(); groundDisabled_ = true; }
        }
        settings_->setBool("/px4/terrain/ready", ready);
        settings_->setBool("/px4/terrain/viewReady", viewReady);
        settings_->setInt("/px4/terrain/requiredTiles", required.size());
        settings_->setInt("/px4/terrain/coveredTiles", covered);
        settings_->setInt("/px4/terrain/pendingTiles", pending_.size());
        settings_->setInt("/px4/terrain/loadedTiles", loaded_.size());
        settings_->setFloat64("/px4/terrain/authorMilliseconds", changed ?
            std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - authorStart).count() : 0.0);
        const std::string status = (config_.source == "procedural" ? "Synthetic terrain: " : "XYZ terrain: ") +
            std::string(!config_.collision ? "visual terrain; " : ready ? "ground ready; " : "waiting for local ground; ") +
            std::to_string(covered) + "/" + std::to_string(required.size()) + " collision tiles; " +
            std::to_string(loaded_.size()) + " loaded, " + std::to_string(pending_.size()) + " awaiting publication";
        settings_->setString("/px4/terrain/status", status.c_str());
    }

    carb::settings::ISettings* settings_{};
    carb::eventdispatcher::ObserverGuard observer_;
    UsdStageWeakPtr stage_;
    px4::terrain::Config config_;
    std::unique_ptr<px4::terrain::Streamer> streamer_;
    std::set<px4::terrain::TileKey> loaded_;
    std::map<px4::terrain::TileKey, px4::terrain::TileMesh> pending_;
    GfVec3d previousPosition_{};
    std::chrono::steady_clock::time_point previousTime_{};
    bool havePosition_{};
    bool groundWasActive_{};
    bool groundDisabled_{};
    std::string lastError_;
};

const carb::PluginImplDesc kPluginImpl = {
    "px4.isaac.terrain.plugin", "Native streamed terrain and collision tiles", "PX4 Isaac", carb::PluginHotReload::eDisabled, "1.0.0"
};

CARB_PLUGIN_IMPL_DEPS(omni::kit::IApp, carb::logging::ILogging, carb::settings::ISettings, carb::eventdispatcher::IEventDispatcher)
CARB_PLUGIN_IMPL(kPluginImpl, TerrainExtension)
void fillInterface(TerrainExtension&) {}
