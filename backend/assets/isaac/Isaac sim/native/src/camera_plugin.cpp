#include "gst_camera.hpp"

#include <carb/PluginUtils.h>
#include <carb/events/EventsUtils.h>
#include <carb/settings/ISettings.h>
#include <omni/ext/IExt.h>
#include <omni/kit/IApp.h>
#include <omni/usd/UsdContextIncludes.h>
#include <omni/usd/UsdContext.h>
#include <omni/graph/core/ComputeGraph.h>
#include <omni/graph/core/CppWrappers.h>
#include <omni/fabric/IToken.h>
#include <omni/fabric/usd/PathConversion.h>
#include <pxr/usd/usd/primRange.h>
#include <pxr/usd/usd/editContext.h>
#include <pxr/usd/usdGeom/camera.h>
#include <pxr/usd/usdRender/product.h>
#include <pxr/usd/usdRender/var.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace og = omni::graph::core;
namespace {

using Clock = std::chrono::steady_clock;

struct Graph {
    og::GraphObj owner{};
    og::NodeObj wrapper{};
    og::GraphObj graph{};
    std::string path;

    void reset() {
        if (owner.iGraph && owner.iGraph->isValid(owner) && wrapper.iNode && wrapper.iNode->isValid(wrapper))
            owner.iGraph->destroyNode(owner, path.c_str(), false);
        owner = {}; wrapper = {}; graph = {};
    }
};

Graph make_graph(og::ComputeGraph* api, const std::string& path, og::GraphPipelineStage phase) {
    auto count = api->getGraphCountInPipelineStage(phase);
    if (!count) throw std::runtime_error("OmniGraph pipeline is not available");
    std::vector<og::GraphObj> owners(count);
    if (!api->getGraphsInPipelineStage(owners.data(), owners.size(), phase))
        throw std::runtime_error("Cannot enumerate OmniGraph pipeline");
    Graph result;
    result.owner = owners.front();
    result.path = path;
    std::string name = path;
    std::replace(name.begin(), name.end(), '/', '_');
    og::CreateGraphAsNodeOptions options;
    options.nodeName = name.c_str();
    options.graphPath = path.c_str();
    options.evaluatorName = phase == og::kGraphPipelineStage_OnDemand ? "execution" : "push";
    options.pipelineStage = phase;
    options.backByUSD = true;
    options.allowAutoInstancing = false;
    result.wrapper = result.owner.iGraph->createGraphAsNodeV2(result.owner, options);
    if (!result.wrapper.iNode || !result.wrapper.iNode->isValid(result.wrapper))
        throw std::runtime_error("Cannot create camera processing graph");
    result.graph = result.wrapper.iNode->getWrappedGraph(result.wrapper);
    return result;
}

og::NodeObj make_node(Graph& graph, const std::string& name, const char* type) {
    const auto path = graph.path + "/" + name;
    auto result = graph.graph.iGraph->createNode(graph.graph, path.c_str(), type, true);
    if (!result.iNode || !result.iNode->isValid(result))
        throw std::runtime_error(std::string("Cannot create native camera node ") + type);
    return result;
}

og::AttributeObj attr(const og::NodeObj& node, const char* name) {
    auto result = node.iNode->getAttribute(node, name);
    if (!result.iAttribute || !result.iAttribute->isValid(result))
        throw std::runtime_error(std::string("Missing native camera attribute ") + name);
    return result;
}

void connect(const og::NodeObj& source, const char* output, const og::NodeObj& target, const char* input) {
    auto from = attr(source, output);
    auto to = attr(target, input);
    if (!from.iAttribute->connectAttrs(from, to, false))
        throw std::runtime_error(std::string("Cannot connect native camera attribute ") + output);
}

void token(const og::NodeObj& node, const char* name, const char* value) {
    auto field = attr(node, name);
    og::NameToken encoded = omni::fabric::Token::createImmortal(value);
    if (!field.iAttribute->setDefaultValue(field, field.iAttribute->getResolvedType(field), &encoded, 1))
        throw std::runtime_error(std::string("Cannot set native camera token ") + name);
}

template<class T> T read_scalar(const og::NodeObj& node, const og::GraphContextObj& context, const char* name) {
    auto handle = og::getAttributeR(context, node.nodeContextHandle, omni::fabric::Token::createImmortal(name),
                                   og::kAccordingToContextIndex);
    const void* data = nullptr;
    context.iAttributeData->getDataR(&data, context, &handle, 1);
    return data ? *static_cast<const T*>(data) : T{};
}

struct Camera {
    std::string path;
    std::string product;
    pxr::SdfPath product_path;
    omni::usd::UsdContext* context = nullptr;
    pxr::UsdStageWeakPtr stage;
    omni::usd::ViewportHandle viewport = omni::usd::kInvalidViewportHandle;
    std::unique_ptr<px4isaac::GstCameraStream> stream;
    Graph post;
    Graph export_graph;
    og::NodeObj frame{};
    og::NodeObj dispatch{};
    og::NodeObj copy{};
    og::NodeObj exporter{};
    Clock::time_point next{};
    Clock::time_point started = Clock::now();
    bool waiting_reported = false;
    bool first = true;
    std::string snapshot;

    ~Camera() {
        if (context && viewport != omni::usd::kInvalidViewportHandle) context->destroyViewport(viewport);
        export_graph.reset();
        post.reset();
        if (stage && !product.empty()) {
            pxr::UsdEditContext edit(stage, stage->GetSessionLayer());
            stage->RemovePrim(pxr::SdfPath(product));
        }
    }

    void initialize(og::ComputeGraph* api, const pxr::UsdPrim& prim, px4isaac::CameraStreamConfig config,
                    std::size_t index, const std::string& snapshot_path) {
        context = omni::usd::UsdContext::getContext();
        stage = prim.GetStage();
        pxr::UsdEditContext edit(stage, stage->GetSessionLayer());
        path = prim.GetPath().GetString();
        snapshot = snapshot_path;
        product = "/Render/PX4Camera_" + std::to_string(index);
        product_path = pxr::SdfPath(product);
        stream = std::make_unique<px4isaac::GstCameraStream>(config);
        auto render_product = pxr::UsdRenderProduct::Define(stage, pxr::SdfPath(product));
        render_product.CreateProductTypeAttr().Set(pxr::TfToken("raster"));
        render_product.CreateResolutionAttr().Set(pxr::GfVec2i(config.width, config.height));
        render_product.CreateCameraRel().SetTargets({prim.GetPath()});
        auto color = pxr::UsdRenderVar::Define(stage, pxr::SdfPath("/Render/Vars/LdrColorSD"));
        color.CreateSourceNameAttr().Set(std::string("LdrColorSD"));
        color.CreateDataTypeAttr().Set(pxr::TfToken("color4f"));
        render_product.CreateOrderedVarsRel().SetTargets({color.GetPath()});
        post = make_graph(api, product + "/PostRender/SDGPipeline", og::kGraphPipelineStage_PostRender);
        render_product.GetPrim().CreateAttribute(pxr::TfToken("ogPostProcessPath"), pxr::SdfValueTypeNames->String)
            .Set(product + "/PostRender");
        auto entry = make_node(post, "Entry", "omni.graph.nodes.GpuInteropRenderProductEntry");
        copy = make_node(post, "Host", "omni.syntheticdata.SdPostRenderVarToHost");
        token(copy, "inputs:renderVar", "LdrColorSD");
        for (const auto* name : {"exec", "rp", "gpu"})
            connect(entry, (std::string("outputs:") + name).c_str(), copy, (std::string("inputs:") + name).c_str());
        export_graph = make_graph(api, product + "/PostProcess/SDGPipeline", og::kGraphPipelineStage_OnDemand);
        frame = make_node(export_graph, "Frame", "omni.syntheticdata.SdOnNewFrame");
        dispatch = make_node(export_graph, "Dispatch", "omni.syntheticdata.SdOnNewRenderProductFrame");
        exporter = make_node(export_graph, "RGBA", "omni.syntheticdata.SdRenderVarToRawArray");
        token(dispatch, "inputs:renderProductPath", product.c_str());
        token(exporter, "inputs:renderVar", "LdrColorSDhost");
        for (const auto* name : {"exec", "renderProductPaths", "renderProductDataPtrs"})
            connect(frame, (std::string("outputs:") + name).c_str(), dispatch, (std::string("inputs:") + name).c_str());
        for (const auto* name : {"exec", "renderResults", "cudaStream"})
            connect(dispatch, (std::string("outputs:") + name).c_str(), exporter, (std::string("inputs:") + name).c_str());
        std::uint32_t engine_id = 0;
        viewport = context->createViewport(context->getDefaultHydraEngineName(), config.fps,
            static_cast<omni::usd::hydra::EngineCreationFlags>(0), 0, &engine_id);
        if (viewport == omni::usd::kInvalidViewportHandle) throw std::runtime_error("Cannot create native RTX camera viewport");
        std::printf("[px4.camera] %s -> RTP/H.264 %s:%d %dx%d %d FPS (%s)\n", path.c_str(), config.host.c_str(),
                    config.port, config.width, config.height, config.fps, stream->encoder_name().c_str());
        std::fflush(stdout);
    }

    void update(carb::settings::ISettings* settings) {
        context->addRender(viewport, omni::fabric::sdfPathToHandle(product_path));
        context->getRenderResults(viewport, false);
        export_graph.graph.iGraph->evaluate(export_graph.graph);
        const auto now = Clock::now();
        if (now < next) return;
        const og::GraphContextObj* contexts = nullptr;
        const og::NodeObj* instances = nullptr;
        if (!exporter.iNode->getAutoInstances(exporter, contexts, instances)) return;
        const auto& graph_context = contexts[0];
        const auto& instance = instances[0];
        auto width = read_scalar<std::uint32_t>(instance, graph_context, "outputs:width");
        auto height = read_scalar<std::uint32_t>(instance, graph_context, "outputs:height");
        const auto buffer_size = read_scalar<std::uint64_t>(instance, graph_context, "outputs:bufferSize");
        if (!width && !height && buffer_size) {
            width = static_cast<std::uint32_t>(stream->config().width);
            height = static_cast<std::uint32_t>(stream->config().height);
        }
        const auto strides = read_scalar<pxr::GfVec2i>(instance, graph_context, "outputs:strides");
        auto stride = static_cast<std::uint32_t>(std::max(0, strides[1]));
        if (width == 0 || height == 0) {
            if (!waiting_reported && now - started > std::chrono::seconds(10)) {
                std::fprintf(stderr, "[px4.camera] Waiting for RTX render data for %s; processed host/frame/dispatch/export=%zu/%zu/%zu/%zu\n",
                    path.c_str(), copy.iNode->getComputeCount(copy), frame.iNode->getComputeCount(frame),
                    dispatch.iNode->getComputeCount(dispatch), exporter.iNode->getComputeCount(exporter));
                waiting_reported = true;
            }
            return;
        }
        if (width != static_cast<std::uint32_t>(stream->config().width) || height != static_cast<std::uint32_t>(stream->config().height))
            throw std::runtime_error("RTX camera resolution differs from the configured stream");
        auto handle = og::getAttributeR(graph_context, instance.nodeContextHandle,
            omni::fabric::Token::createImmortal("outputs:data"), og::kAccordingToContextIndex);
        std::size_t size = 0;
        graph_context.iAttributeData->getElementCount(&size, graph_context, &handle, 1);
        const void* value = nullptr;
        graph_context.iAttributeData->getDataR(&value, graph_context, &handle, 1);
        if (!value || !size) return;
        const auto* bytes = *static_cast<const std::uint8_t* const*>(value);
        if (!bytes) return;
        if (size == static_cast<std::size_t>(width) * height * 4 || !stride) stride = width * 4;
        const auto timestamp = std::chrono::duration_cast<std::chrono::nanoseconds>(now.time_since_epoch()).count();
        stream->push_rgba(bytes, size, stride, timestamp);
        next = now + std::chrono::nanoseconds(1000000000 / stream->config().fps);
        settings->setInt64("/px4/camera/frames", static_cast<std::int64_t>(stream->frames()));
        if (first) {
            first = false;
            std::printf("[px4.camera] Native RTX RGBA frames streaming for %s (%zu bytes, stride %u, format %llu)\n",
                path.c_str(), size, stride,
                static_cast<unsigned long long>(read_scalar<std::uint64_t>(instance, graph_context, "outputs:format")));
            std::fflush(stdout);
        }
        if (!snapshot.empty() && stream->frames() >= 90) {
            std::ofstream out(snapshot, std::ios::binary);
            out << "P6\n" << width << ' ' << height << "\n255\n";
            for (std::size_t y = 0; y < height; ++y)
                for (std::size_t x = 0; x < width; ++x)
                    out.write(reinterpret_cast<const char*>(bytes + stride * y + 4 * x), 3);
            if (!out) throw std::runtime_error("Cannot write native camera snapshot");
            snapshot.clear();
        }
    }
};

struct State {
    carb::events::ISubscriptionPtr subscription;
    carb::settings::ISettings* settings = nullptr;
    omni::kit::IApp* app = nullptr;
    og::ComputeGraph* graph = nullptr;
    pxr::UsdStageWeakPtr stage;
    std::map<std::string, std::unique_ptr<Camera>> cameras;
    Clock::time_point next_scan{};
    std::size_t next_id = 0;
    bool failed = false;

    std::string setting(const char* name, const char* fallback) {
        const auto* value = settings->getStringBuffer(name);
        return value ? value : fallback;
    }

    template<class T> T value(const pxr::UsdPrim& prim, const char* name, T fallback) {
        auto attribute = prim.GetAttribute(pxr::TfToken(name));
        T result;
        return attribute && attribute.Get(&result) ? result : fallback;
    }

    void scan(const pxr::UsdStageWeakPtr& current) {
        std::map<std::string, pxr::UsdPrim> desired;
        const auto default_path = setting("/px4/camera/path", "/World/Aircraft/OnboardCamera");
        for (auto prim : current->Traverse()) {
            if (!prim.IsA<pxr::UsdGeomCamera>()) continue;
            const auto path = prim.GetPath().GetString();
            if (value<bool>(prim, "px4:camera:enabled", path == default_path)) desired.emplace(path, prim);
        }
        for (auto it = cameras.begin(); it != cameras.end();) {
            if (!desired.count(it->first)) it = cameras.erase(it);
            else ++it;
        }
        const auto base_port = settings->getAsInt("/px4/camera/port");
        std::map<int, std::string> ports;
        auto reserve_port = [&](int port, const std::string& path) {
            auto found = ports.find(port);
            if (found != ports.end() && found->second != path)
                throw std::runtime_error("Camera UDP port " + std::to_string(port) + " is assigned to more than one camera");
            ports[port] = path;
        };
        for (const auto& item : cameras) reserve_port(item.second->stream->config().port, item.first);
        for (const auto& item : desired) {
            int port = 0;
            auto attribute = item.second.GetAttribute(pxr::TfToken("px4:camera:port"));
            if (attribute && attribute.Get(&port)) reserve_port(port, item.first);
            else if (item.first == default_path) reserve_port(base_port, item.first);
        }
        for (const auto& item : desired) {
            if (cameras.count(item.first)) continue;
            px4isaac::CameraStreamConfig config;
            config.host = value<std::string>(item.second, "px4:camera:host", setting("/px4/camera/host", "127.0.0.1"));
            config.encoder = value<std::string>(item.second, "px4:camera:encoder", setting("/px4/camera/encoder", "x264"));
            auto port = item.second.GetAttribute(pxr::TfToken("px4:camera:port"));
            if (!port || !port.Get(&config.port)) {
                config.port = base_port;
                if (item.first != default_path) {
                    if (base_port < 1 || base_port >= 65535) throw std::runtime_error("No available camera UDP port");
                    config.port = base_port + 1;
                    while (ports.count(config.port) && config.port < 65535) ++config.port;
                }
            }
            reserve_port(config.port, item.first);
            config.width = value<int>(item.second, "px4:camera:width", settings->getAsInt("/px4/camera/width"));
            config.height = value<int>(item.second, "px4:camera:height", settings->getAsInt("/px4/camera/height"));
            config.fps = value<int>(item.second, "px4:camera:fps", settings->getAsInt("/px4/camera/fps"));
            config.bitrate_kbps = value<int>(item.second, "px4:camera:bitrate", settings->getAsInt("/px4/camera/bitrate"));
            auto camera = std::make_unique<Camera>();
            camera->initialize(graph, item.second, config, next_id++, setting("/px4/camera/snapshot", ""));
            cameras.emplace(item.first, std::move(camera));
        }
    }

    void update() {
        if (failed) {
            if (!settings->getAsBool("/px4/camera/enabled")) failed = false;
            return;
        }
        try {
            if (!app->isAppReady()) return;
            auto* context = omni::usd::UsdContext::getContext();
            if (!context || context->getStageState() != omni::usd::StageState::eOpened) return;
            auto current = context ? context->getStage() : pxr::UsdStageWeakPtr{};
            if (current != stage) {
                cameras.clear();
                stage = current;
                next_scan = {};
            }
            if (!settings->getAsBool("/px4/camera/enabled")) { cameras.clear(); return; }
            if (!current) return;
            const auto now = Clock::now();
            if (now >= next_scan) { scan(current); next_scan = now + std::chrono::seconds(1); }
            for (auto& item : cameras) item.second->update(settings);
        } catch (const std::exception& error) {
            std::fprintf(stderr, "[px4.camera] %s\n", error.what());
            settings->setString("/px4/camera/error", error.what());
            cameras.clear();
            failed = true;
        }
    }
};

std::unique_ptr<State> state;

class CameraExtension : public omni::ext::IExt {
public:
    void onStartup(const char*) override {
        state = std::make_unique<State>();
        state->settings = carb::getFramework()->acquireInterface<carb::settings::ISettings>();
        state->graph = carb::getFramework()->acquireInterface<og::ComputeGraph>();
        state->app = carb::getFramework()->acquireInterface<omni::kit::IApp>();
        state->subscription = carb::events::createSubscriptionToPop(state->app->getUpdateEventStream(),
            [](carb::events::IEvent*) { if (state) state->update(); }, 20, "px4.isaac.camera");
    }
    void onShutdown() override {
        if (state) state->subscription.reset();
        state.reset();
    }
};

}

const carb::PluginImplDesc camera_plugin_desc{"px4.isaac.camera.plugin", "Native RTX camera RTP streaming", "PX4 Isaac",
                                              carb::PluginHotReload::eDisabled, "1.0.0"};
CARB_PLUGIN_IMPL(camera_plugin_desc, CameraExtension)
CARB_PLUGIN_IMPL_DEPS(carb::settings::ISettings, omni::kit::IApp, og::ComputeGraph)
void fillInterface(CameraExtension&) {}
