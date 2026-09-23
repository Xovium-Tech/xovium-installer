#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

namespace px4isaac {

struct CameraStreamConfig {
    std::string host = "127.0.0.1";
    std::string encoder = "x264";
    int port = 5600;
    int width = 640;
    int height = 480;
    int fps = 30;
    int bitrate_kbps = 4000;
};

class GstCameraStream {
public:
    explicit GstCameraStream(CameraStreamConfig config);
    ~GstCameraStream();
    GstCameraStream(const GstCameraStream&) = delete;
    GstCameraStream& operator=(const GstCameraStream&) = delete;
    void push_rgba(const std::uint8_t* data, std::size_t size, std::size_t stride, std::uint64_t timestamp_ns);
    const CameraStreamConfig& config() const;
    const std::string& encoder_name() const;
    std::uint64_t frames() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}
