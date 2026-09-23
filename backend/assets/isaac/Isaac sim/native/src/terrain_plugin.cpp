#define CARB_EXPORTS

#include "terrain.hpp"

#include <carb/PluginUtils.h>
#include <carb/eventdispatcher/IEventDispatcher.h>
#include <carb/logging/Log.h>
#include <carb/settings/ISettings.h>
#include <omni/ext/IExt.h>
#include <omni/kit/IApp.h>
#include <omni/usd/UsdContextIncludes.h>
#include <omni/usd/UsdContext.h>
#include <pxr/usd/usd/editContext.h>
#include <pxr/usd/usdGeom/mesh.h>
#include <pxr/usd/usdGeom/metrics.h>
#include <pxr/usd/usdGeom/primvarsAPI.h>
#include <pxr/usd/usdGeom/xform.h>
#include <pxr/usd/usdGeom/xformCache.h>
#include <pxr/usd/usdPhysics/collisionAPI.h>
#include <pxr/usd/usdPhysics/materialAPI.h>
#include <pxr/usd/usdPhysics/meshCollisionAPI.h>
#include <pxr/usd/usdShade/materialBindingAPI.h>

#include <cmath>
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
        settings_->setBool("/px4/terrain/ready", false);
        settings_->setString("/px4/terrain/status", "Waiting for aircraft stage");
        auto* dispatcher = carb::getCachedInterface<carb::eventdispatcher::IEventDispatcher>();
        observer_ = dispatcher->observeEvent(carb::RStringKey("px4.isaac.terrain.update"), 10,
            omni::kit::kGlobalEventUpdate, [this](const carb::eventdispatcher::Event&) {
                try { update(); }
                catch (const std::exception& error) {
                    settings_->setBool("/px4/terrain/ready", false);
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
        groundDisabled_ = false;
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
        if (!aircraft) { settings_->setBool("/px4/terrain/ready", false); return; }
        UsdGeomXformCache transforms;
        const auto position = transforms.GetLocalToWorldTransform(aircraft).ExtractTranslation();
        const auto desired = px4::terrain::desiredTiles(position[0], position[1], config_);
        const std::set<px4::terrain::TileKey> wanted(desired.begin(), desired.end());
        streamer_->request(desired);
        UsdEditContext edit(stage_, stage_->GetSessionLayer());
        for (auto iterator = loaded_.begin(); iterator != loaded_.end();)
            if (!wanted.count(*iterator)) { stage_->RemovePrim(tilePath(*iterator)); iterator = loaded_.erase(iterator); }
            else ++iterator;
        for (auto& result : streamer_->collect()) {
            if (!result.error.empty()) {
                settings_->setString("/px4/terrain/status", result.error.c_str());
                CARB_LOG_WARN("Terrain tile %d/%d/%d: %s", result.key.zoom, result.key.x, result.key.y, result.error.c_str());
            } else { authorTile(stage_, result.mesh, config_.collision); loaded_.insert(result.key); }
        }
        const bool ready = !desired.empty() && loaded_.size() == wanted.size();
        settings_->setBool("/px4/terrain/ready", ready);
        if (ready && !groundDisabled_) {
            const auto ground = stage_->GetPrimAtPath(SdfPath("/World/Ground"));
            if (ground) { ground.SetActive(false); groundDisabled_ = true; }
        }
        if (ready) {
            const std::string status = (config_.source == "procedural" ? "Synthetic terrain: " : "XYZ terrain: ") +
                std::to_string(loaded_.size()) + "/" + std::to_string(desired.size()) + " tiles";
            settings_->setString("/px4/terrain/status", status.c_str());
        }
    }

    carb::settings::ISettings* settings_{};
    carb::eventdispatcher::ObserverGuard observer_;
    UsdStageWeakPtr stage_;
    px4::terrain::Config config_;
    std::unique_ptr<px4::terrain::Streamer> streamer_;
    std::set<px4::terrain::TileKey> loaded_;
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
