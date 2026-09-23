#include "gst_camera.hpp"

#include <gst/app/gstappsrc.h>
#include <gst/gst.h>

#include <algorithm>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace px4isaac {

struct GstCameraStream::Impl {
    CameraStreamConfig config;
    GstElement* pipeline = nullptr;
    GstElement* source = nullptr;
    GstBus* bus = nullptr;
    std::string encoder;
    std::uint64_t count = 0;
    std::uint64_t first_ns = 0;
    std::uint64_t last_pts = 0;

    explicit Impl(CameraStreamConfig value) : config(std::move(value)) {
        static std::once_flag initialized;
        std::call_once(initialized, [] {
            GError* error = nullptr;
            if (!gst_init_check(nullptr, nullptr, &error)) {
                std::string message = error ? error->message : "unknown initialization error";
                if (error) g_error_free(error);
                throw std::runtime_error("GStreamer: " + message);
            }
        });
        if (config.width < 2 || config.height < 2 || config.width > 8192 || config.height > 8192 ||
            config.width % 2 || config.height % 2 || config.fps < 1 || config.fps > 120 ||
            config.port < 1 || config.port > 65535 || config.bitrate_kbps < 64 || config.bitrate_kbps > 100000 ||
            config.host.empty() || (config.encoder != "x264" && config.encoder != "auto" && config.encoder != "nvenc")) {
            throw std::invalid_argument("Invalid camera stream configuration");
        }
        try {
            start(config.encoder != "x264");
        } catch (...) {
            stop();
            if (config.encoder == "x264") throw;
            try { start(false); }
            catch (...) { stop(); throw; }
        }
    }

    ~Impl() { stop(); }

    void stop() {
        if (pipeline) {
            gst_element_set_state(pipeline, GST_STATE_NULL);
            gst_object_unref(pipeline);
        }
        if (bus) gst_object_unref(bus);
        pipeline = nullptr;
        source = nullptr;
        bus = nullptr;
    }

    GstElement* add(const char* factory, const char* name) {
        GstElement* element = gst_element_factory_make(factory, name);
        if (!element) throw std::runtime_error(std::string("Missing GStreamer element: ") + factory);
        if (!gst_bin_add(GST_BIN(pipeline), element)) {
            gst_object_unref(element);
            throw std::runtime_error("Cannot add GStreamer element");
        }
        return element;
    }

    void start(bool hardware) {
        pipeline = gst_pipeline_new(nullptr);
        if (!pipeline) throw std::runtime_error("Cannot allocate GStreamer pipeline");
        source = add("appsrc", "frames");
        auto* queue = add("queue", "latest_frames");
        auto* convert = add("videoconvert", "convert");
        auto* filter = add("capsfilter", "i420");
        encoder = "x264enc";
        if (hardware) {
            for (const char* candidate : {"nvautogpuh264enc", "nvh264enc"}) {
                auto* factory = gst_element_factory_find(candidate);
                if (factory) {
                    encoder = candidate;
                    gst_object_unref(factory);
                    break;
                }
            }
        }
        auto* enc = add(encoder.c_str(), "encoder");
        auto* parser = add("h264parse", "parser");
        auto* payload = add("rtph264pay", "rtp");
        auto* sink = add("udpsink", "udp");
        auto* caps = gst_caps_new_simple("video/x-raw", "format", G_TYPE_STRING, "RGBA",
                                         "width", G_TYPE_INT, config.width, "height", G_TYPE_INT, config.height,
                                         "framerate", GST_TYPE_FRACTION, config.fps, 1, nullptr);
        gst_app_src_set_caps(GST_APP_SRC(source), caps);
        gst_caps_unref(caps);
        g_object_set(source, "is-live", TRUE, "format", GST_FORMAT_TIME, "block", FALSE,
                     "max-buffers", guint64(2), "max-bytes", guint64(0), "max-time", guint64(0),
                     "leaky-type", GST_APP_LEAKY_TYPE_DOWNSTREAM, "emit-signals", FALSE, nullptr);
        g_object_set(queue, "max-size-buffers", guint(2), "max-size-bytes", guint(0),
                     "max-size-time", guint64(0), "leaky", 2, nullptr);
        caps = gst_caps_new_simple("video/x-raw", "format", G_TYPE_STRING, encoder == "x264enc" ? "I420" : "NV12", nullptr);
        g_object_set(filter, "caps", caps, nullptr);
        gst_caps_unref(caps);
        g_object_set(enc, "bitrate", guint(config.bitrate_kbps), nullptr);
        if (encoder == "x264enc") {
            gst_util_set_object_arg(G_OBJECT(enc), "tune", "zerolatency");
            gst_util_set_object_arg(G_OBJECT(enc), "speed-preset", "ultrafast");
            g_object_set(enc, "key-int-max", guint(config.fps), "bframes", guint(0), "byte-stream", TRUE, nullptr);
        } else {
            if (g_object_class_find_property(G_OBJECT_GET_CLASS(enc), "zerolatency"))
                g_object_set(enc, "zerolatency", TRUE, nullptr);
            if (g_object_class_find_property(G_OBJECT_GET_CLASS(enc), "gop-size"))
                g_object_set(enc, "gop-size", config.fps, nullptr);
        }
        g_object_set(payload, "pt", guint(96), "config-interval", -1, "mtu", guint(1200), nullptr);
        g_object_set(sink, "host", config.host.c_str(), "port", config.port, "sync", FALSE, "async", FALSE, nullptr);
        if (!gst_element_link_many(source, queue, convert, filter, enc, parser, payload, sink, nullptr))
            throw std::runtime_error("Cannot link GStreamer camera pipeline");
        bus = gst_element_get_bus(pipeline);
        if (gst_element_set_state(pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
            throw std::runtime_error("Cannot start GStreamer encoder " + encoder);
        check_bus();
    }

    void check_bus() {
        auto* message = gst_bus_pop_filtered(bus, GST_MESSAGE_ERROR);
        if (!message) return;
        GError* error = nullptr;
        gchar* details = nullptr;
        gst_message_parse_error(message, &error, &details);
        std::string text = error ? error->message : "unknown stream error";
        if (error) g_error_free(error);
        g_free(details);
        gst_message_unref(message);
        throw std::runtime_error("GStreamer camera: " + text);
    }

    void push(const std::uint8_t* data, std::size_t size, std::size_t stride, std::uint64_t timestamp_ns) {
        const auto row_size = static_cast<std::size_t>(config.width) * 4;
        const auto height = static_cast<std::size_t>(config.height);
        if (!data || stride < row_size || size < row_size || height - 1 > (size - row_size) / stride)
            throw std::invalid_argument("Camera RGBA frame size or row stride is invalid");
        try {
            check_bus();
        } catch (...) {
            if (encoder == "x264enc" || config.encoder == "x264") throw;
            stop();
            start(false);
            count = 0;
        }
        auto* buffer = gst_buffer_new_allocate(nullptr, row_size * height, nullptr);
        if (!buffer) throw std::runtime_error("Cannot allocate camera frame buffer");
        GstMapInfo map{};
        if (!gst_buffer_map(buffer, &map, GST_MAP_WRITE)) {
            gst_buffer_unref(buffer);
            throw std::runtime_error("Cannot map camera frame buffer");
        }
        for (std::size_t y = 0; y < height; ++y)
            std::memcpy(map.data + y * row_size, data + y * stride, row_size);
        gst_buffer_unmap(buffer, &map);
        if (count == 0) first_ns = timestamp_ns;
        auto pts = timestamp_ns >= first_ns ? timestamp_ns - first_ns : 0;
        if (count && pts <= last_pts) pts = last_pts + GST_SECOND / config.fps;
        GST_BUFFER_PTS(buffer) = pts;
        GST_BUFFER_DTS(buffer) = GST_CLOCK_TIME_NONE;
        GST_BUFFER_DURATION(buffer) = GST_SECOND / config.fps;
        GST_BUFFER_OFFSET(buffer) = count;
        const auto result = gst_app_src_push_buffer(GST_APP_SRC(source), buffer);
        if (result != GST_FLOW_OK) throw std::runtime_error("GStreamer rejected camera frame: " + std::to_string(result));
        last_pts = pts;
        ++count;
    }
};

GstCameraStream::GstCameraStream(CameraStreamConfig config) : impl_(std::make_unique<Impl>(std::move(config))) {}
GstCameraStream::~GstCameraStream() = default;
void GstCameraStream::push_rgba(const std::uint8_t* data, std::size_t size, std::size_t stride, std::uint64_t timestamp_ns) {
    impl_->push(data, size, stride, timestamp_ns);
}
const CameraStreamConfig& GstCameraStream::config() const { return impl_->config; }
const std::string& GstCameraStream::encoder_name() const { return impl_->encoder; }
std::uint64_t GstCameraStream::frames() const { return impl_->count; }

}
