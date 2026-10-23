#define CARB_EXPORTS
#include <carb/PluginUtils.h>
#include <carb/eventdispatcher/IEventDispatcher.h>
#include <carb/settings/ISettings.h>
#include <omni/ext/IExt.h>
#include <omni/kit/IApp.h>
#include <string>

// The legacy extension retains its public settings and flight readiness contract.
// Rendering, workers and cache ownership live in the standalone extension.
class TerrainCompatibility final : public omni::ext::IExt {
public:
    void onStartup(const char*) override {
        settings_ = carb::getCachedInterface<carb::settings::ISettings>();
        for (const auto* key : {"source", "cacheDirectory", "imageryUrl", "elevationUrl", "imageryExtension", "targetPath"}) {
            const char* value = settings_->getStringBuffer(legacy(key).c_str());
            settings_->setString(generic(key).c_str(), value ? value : "");
        }
        for (const auto* key : {"originLatitude", "originLongitude", "originAltitude", "tileSize", "syntheticHeight", "visualOuterRadiusM", "prefetchSeconds"})
            settings_->setFloat64(generic(key).c_str(), settings_->getAsFloat64(legacy(key).c_str()));
        for (const auto* key : {"seed", "zoom", "radius", "meshCells", "visualMaxTiles"})
            settings_->setInt(generic(key).c_str(), settings_->getAsInt(legacy(key).c_str()));
        settings_->setBool(generic("collision").c_str(), settings_->getAsBool(legacy("collision").c_str()));
        settings_->setBool(generic("enabled").c_str(), settings_->getAsBool(legacy("enabled").c_str()));
        settings_->setString(generic("rootPath").c_str(), "/World/StreamedTerrain");
        settings_->setString(generic("fallbackPath").c_str(), "/World/Ground");
        settings_->setInt(generic("reload").c_str(), settings_->getAsInt(generic("reload").c_str()) + 1);
        settings_->setBool(legacy("active").c_str(), true);
        settings_->setBool(legacy("ready").c_str(), false);
        observer_ = carb::getCachedInterface<carb::eventdispatcher::IEventDispatcher>()->observeEvent(
            carb::RStringKey("px4.isaac.terrain.compatibility"), 11, omni::kit::kGlobalEventUpdate,
            [this](const carb::eventdispatcher::Event&) { update(); });
    }
    void onShutdown() override {
        observer_.reset();
        if (settings_) {
            settings_->setBool(generic("enabled").c_str(), false);
            settings_->setBool(legacy("active").c_str(), false);
            settings_->setBool(legacy("ready").c_str(), false);
        }
    }
private:
    static std::string legacy(const char* key) { return std::string("/px4/terrain/") + key; }
    static std::string generic(const char* key) { return std::string("/exts/xovium.dynamic_terrain/") + key; }
    void update() {
        const bool enabled = settings_->getAsBool(legacy("enabled").c_str());
        settings_->setBool(generic("enabled").c_str(), enabled);
        for (const auto* key : {"active", "ready", "viewReady", "visualReady"})
            settings_->setBool(legacy(key).c_str(), enabled && settings_->getAsBool(generic(key).c_str()));
        for (const auto* key : {"requiredTiles", "coveredTiles", "pendingTiles", "loadedTiles",
             "workerQueued", "workerActive", "readyQueue", "tileRetries", "decodedDemBytes", "decodedDemEntries",
             "decodedDemHits", "decodedDemDecodes", "downloadQueued", "downloadActive", "downloadPeakQueued",
             "downloadPeakActive", "downloadBufferedBytes", "downloadCacheHits", "downloaded", "downloadFailed",
             "downloadCancelled", "downloadBytes", "downloadConnections", "downloadRetryCount",
             "visualTiles", "visualPendingTiles", "visualStagingTiles", "visualWorkerQueued", "visualWorkerActive",
             "prefetchTiles", "retainedViewTiles"})
            settings_->setInt(legacy(key).c_str(), settings_->getAsInt(generic(key).c_str()));
        settings_->setFloat64(legacy("authorMilliseconds").c_str(), settings_->getAsFloat64(generic("authorMilliseconds").c_str()));
        const char* status = settings_->getStringBuffer(generic("status").c_str());
        settings_->setString(legacy("status").c_str(), status ? status : "");
    }
    carb::settings::ISettings* settings_{};
    carb::eventdispatcher::ObserverGuard observer_;
};
const carb::PluginImplDesc kPluginImpl = {
    "px4.isaac.terrain.plugin", "Compatibility settings for Xovium Dynamic Terrain", "Xovium Tech", carb::PluginHotReload::eDisabled, "1.1.0"
};
CARB_PLUGIN_IMPL_DEPS(omni::kit::IApp, carb::settings::ISettings, carb::eventdispatcher::IEventDispatcher)
CARB_PLUGIN_IMPL(kPluginImpl, TerrainCompatibility)
void fillInterface(TerrainCompatibility&) {}
