#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

namespace px4::terrain {

struct TileKey {
    int x{};
    int y{};
    int zoom{};
    bool operator<(const TileKey& other) const;
    bool operator==(const TileKey& other) const;
};

struct Config {
    std::string source{"procedural"};
    std::filesystem::path cacheDirectory;
    std::string imageryUrl;
    std::string elevationUrl;
    std::string imageryExtension{"png"};
    double originLatitude{47.397742};
    double originLongitude{8.545594};
    double originAltitude{489.4};
    double tileSize{256.0};
    double syntheticHeight{25.0};
    int seed{7};
    int zoom{14};
    int radius{1};
    int meshCells{32};
    bool collision{true};
    void validate() const;
};

struct TileMesh {
    TileKey key;
    std::vector<std::array<float, 3>> points;
    std::vector<std::array<float, 2>> textureCoordinates;
    std::vector<int> indices;
    std::filesystem::path textureFile;
};

struct TileResult {
    TileKey key;
    TileMesh mesh;
    std::string error;
};

std::array<double, 2> geographicToTile(double latitude, double longitude, int zoom);
std::array<double, 2> tileToGeographic(double x, double y, int zoom);
std::array<double, 3> geographicToEnu(double latitude, double longitude, double altitude, const Config& config);
double terrariumHeight(std::uint8_t red, std::uint8_t green, std::uint8_t blue);
std::string expandUrl(const std::string& pattern, const TileKey& key);
double syntheticElevation(double east, double north, const Config& config);
std::vector<TileKey> desiredTiles(double east, double north, const Config& config);
TileMesh buildTile(const TileKey& key, const Config& config, const std::atomic_bool* cancelled = nullptr);

class Streamer {
public:
    explicit Streamer(Config config);
    ~Streamer();
    Streamer(const Streamer&) = delete;
    Streamer& operator=(const Streamer&) = delete;
    void request(const std::vector<TileKey>& desired);
    std::vector<TileResult> collect();
private:
    void work();
    Config config_;
    std::atomic_bool stopping_{false};
    std::mutex mutex_;
    std::condition_variable condition_;
    std::set<TileKey> desired_;
    std::set<TileKey> scheduled_;
    std::deque<TileKey> queue_;
    std::deque<TileResult> ready_;
    std::map<TileKey, std::chrono::steady_clock::time_point> retries_;
    std::thread worker_;
};

}
