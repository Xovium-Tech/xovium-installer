#include "terrain.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <tuple>

#include <curl/curl.h>
#include <png.h>

namespace px4::terrain {
namespace {
constexpr double pi = 3.14159265358979323846;
constexpr double radiusEarth = 6378137.0;
constexpr std::size_t maxDownload = 32 * 1024 * 1024;

double radians(double degrees) { return degrees * pi / 180.0; }
double degrees(double angle) { return angle * 180.0 / pi; }

struct Image {
    unsigned width{};
    unsigned height{};
    std::vector<std::uint8_t> pixels;
};

std::array<double, 3> ecef(double latitude, double longitude, double altitude) {
    const double lat = radians(latitude), lon = radians(longitude);
    constexpr double e2 = 6.6943799901413165e-3;
    const double n = radiusEarth / std::sqrt(1 - e2 * std::sin(lat) * std::sin(lat));
    return {(n + altitude) * std::cos(lat) * std::cos(lon),
            (n + altitude) * std::cos(lat) * std::sin(lon),
            (n * (1 - e2) + altitude) * std::sin(lat)};
}

std::string fingerprint(const std::string& input) {
    std::uint64_t hash = 14695981039346656037ULL;
    for (unsigned char byte : input) { hash ^= byte; hash *= 1099511628211ULL; }
    std::ostringstream result;
    result << std::hex << hash;
    return result.str();
}

std::filesystem::path cachedPath(const Config& config, const std::string& source, const TileKey& key, const std::string& extension) {
    return config.cacheDirectory / fingerprint(source) / std::to_string(key.zoom) /
           std::to_string(key.x) / (std::to_string(key.y) + "." + extension);
}

Image readPng(const std::filesystem::path& path) {
    png_image image{};
    image.version = PNG_IMAGE_VERSION;
    if (!png_image_begin_read_from_file(&image, path.c_str()))
        throw std::runtime_error("Elevation tile is not a readable PNG");
    if (image.width < 2 || image.height < 2 || image.width > 4096 || image.height > 4096) {
        png_image_free(&image);
        throw std::runtime_error("Tile image dimensions must be between 2 and 4096 pixels");
    }
    image.format = PNG_FORMAT_RGBA;
    Image result{image.width, image.height, std::vector<std::uint8_t>(PNG_IMAGE_SIZE(image))};
    if (!png_image_finish_read(&image, nullptr, result.pixels.data(), 0, nullptr)) {
        png_image_free(&image);
        throw std::runtime_error("Cannot decode elevation PNG");
    }
    png_image_free(&image);
    return result;
}

void writePng(const std::filesystem::path& path, const Image& data) {
    std::filesystem::create_directories(path.parent_path());
    png_image image{};
    image.version = PNG_IMAGE_VERSION;
    image.width = data.width;
    image.height = data.height;
    image.format = PNG_FORMAT_RGBA;
    const auto temporary = path.string() + ".part";
    if (!png_image_write_to_file(&image, temporary.c_str(), 0, data.pixels.data(), 0, nullptr))
        throw std::runtime_error("Cannot write terrain texture cache");
    std::filesystem::rename(temporary, path);
}

struct Transfer {
    std::vector<char> bytes;
    const std::atomic_bool* cancelled;
};

std::size_t receive(char* pointer, std::size_t size, std::size_t count, void* user) {
    auto& transfer = *static_cast<Transfer*>(user);
    const std::size_t bytes = size * count;
    if (bytes > maxDownload || transfer.bytes.size() > maxDownload - bytes ||
        (transfer.cancelled && transfer.cancelled->load())) return 0;
    transfer.bytes.insert(transfer.bytes.end(), pointer, pointer + bytes);
    return bytes;
}

int progress(void* user, curl_off_t, curl_off_t, curl_off_t, curl_off_t) {
    const auto* cancelled = static_cast<const std::atomic_bool*>(user);
    return cancelled && cancelled->load() ? 1 : 0;
}

std::filesystem::path obtain(const Config& config, const std::string& pattern, const TileKey& key,
                             const std::string& extension, const std::atomic_bool* cancelled) {
    if (cancelled && cancelled->load()) throw std::runtime_error("Terrain request cancelled");
    const auto destination = cachedPath(config, pattern, key, extension);
    if (std::filesystem::is_regular_file(destination) && std::filesystem::file_size(destination) > 0)
        return destination;
    const std::string url = expandUrl(pattern, key);
    std::filesystem::create_directories(destination.parent_path());
    const auto temporary = destination.string() + ".part";
    if (url.rfind("file://", 0) == 0 || (!url.empty() && url[0] == '/')) {
        const std::filesystem::path source = url.rfind("file://", 0) == 0 ? url.substr(7) : url;
        if (!std::filesystem::is_regular_file(source) || std::filesystem::file_size(source) > maxDownload)
            throw std::runtime_error("Local terrain tile is missing or exceeds 32 MiB");
        std::filesystem::copy_file(source, temporary, std::filesystem::copy_options::overwrite_existing);
    } else {
        static std::once_flag curlInitialization;
        std::call_once(curlInitialization, [] {
            if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK)
                throw std::runtime_error("Cannot initialize terrain HTTP client");
        });
        CURL* curl = curl_easy_init();
        if (!curl) throw std::runtime_error("Cannot allocate terrain HTTP client");
        Transfer transfer{{}, cancelled};
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_PROTOCOLS_STR, "http,https");
        curl_easy_setopt(curl, CURLOPT_REDIR_PROTOCOLS_STR, "http,https");
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
        curl_easy_setopt(curl, CURLOPT_MAXREDIRS, 3L);
        curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, 4000L);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, 12000L);
        curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
        curl_easy_setopt(curl, CURLOPT_USERAGENT, "px4-isaac-terrain/1.0");
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, receive);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &transfer);
        curl_easy_setopt(curl, CURLOPT_NOPROGRESS, 0L);
        curl_easy_setopt(curl, CURLOPT_XFERINFOFUNCTION, progress);
        curl_easy_setopt(curl, CURLOPT_XFERINFODATA, cancelled);
        const CURLcode error = curl_easy_perform(curl);
        long status = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        curl_easy_cleanup(curl);
        if (error != CURLE_OK || status != 200 || transfer.bytes.empty())
            throw std::runtime_error("Terrain tile download failed (HTTP " + std::to_string(status) + ")");
        std::ofstream output(temporary, std::ios::binary);
        output.write(transfer.bytes.data(), static_cast<std::streamsize>(transfer.bytes.size()));
        if (!output) throw std::runtime_error("Cannot write downloaded terrain tile");
    }
    std::filesystem::rename(temporary, destination);
    return destination;
}

std::uint8_t byte(double value) { return static_cast<std::uint8_t>(std::clamp(value, 0.0, 255.0)); }

std::filesystem::path syntheticTexture(const TileKey& key, const Config& config) {
    const std::string signature = "synthetic-v2:" + std::to_string(config.seed) + ":" +
        std::to_string(config.tileSize) + ":" + std::to_string(config.syntheticHeight);
    const auto path = cachedPath(config, signature, key, "png");
    if (std::filesystem::is_regular_file(path)) return path;
    Image image{128, 128, std::vector<std::uint8_t>(128 * 128 * 4)};
    for (unsigned row = 0; row < image.height; ++row) {
        for (unsigned col = 0; col < image.width; ++col) {
            const double east = (key.x + (col + 0.5) / image.width) * config.tileSize;
            const double north = (key.y + 1.0 - (row + 0.5) / image.height) * config.tileSize;
            const double height = syntheticElevation(east, north, config);
            const double texture = 9 * std::sin(east * 0.39) * std::cos(north * 0.28);
            const int field = static_cast<int>(std::floor(east / 70) + std::floor(north / 90));
            const double variation = field % 2 ? 10 : -10;
            const std::size_t index = 4 * (row * image.width + col);
            image.pixels[index] = byte(62 + height * 0.35 + texture + variation);
            image.pixels[index + 1] = byte(93 + height * 0.2 + texture + variation);
            image.pixels[index + 2] = byte(39 + height * 0.3 + texture);
            image.pixels[index + 3] = 255;
        }
    }
    writePng(path, image);
    return path;
}

}

bool TileKey::operator<(const TileKey& other) const { return std::tie(zoom, x, y) < std::tie(other.zoom, other.x, other.y); }
bool TileKey::operator==(const TileKey& other) const { return x == other.x && y == other.y && zoom == other.zoom; }

void Config::validate() const {
    if (source != "procedural" && source != "xyz") throw std::invalid_argument("Terrain source must be procedural or xyz");
    if (cacheDirectory.empty()) throw std::invalid_argument("Terrain cacheDirectory must be configured");
    if (!std::isfinite(originLatitude) || std::abs(originLatitude) > 80 ||
        !std::isfinite(originLongitude) || std::abs(originLongitude) > 180 || !std::isfinite(originAltitude))
        throw std::invalid_argument("Invalid terrain geographic origin");
    if (zoom < 1 || zoom > 20 || radius < 1 || radius > 3 || meshCells < 4 || meshCells > 128)
        throw std::invalid_argument("Terrain zoom/radius/meshCells outside supported bounds");
    if (!std::isfinite(tileSize) || tileSize < 32 || tileSize > 4096 ||
        !std::isfinite(syntheticHeight) || syntheticHeight < 0 || syntheticHeight > 500)
        throw std::invalid_argument("Invalid synthetic terrain dimensions");
    if (source == "xyz" && (imageryUrl.empty() || elevationUrl.empty()))
        throw std::invalid_argument("XYZ mode requires imageryUrl and Terrarium elevationUrl");
    if (imageryExtension != "png" && imageryExtension != "jpg" && imageryExtension != "jpeg")
        throw std::invalid_argument("Imagery extension must be png, jpg or jpeg");
}

std::array<double, 2> geographicToTile(double latitude, double longitude, int zoom) {
    const double count = std::ldexp(1.0, zoom);
    const double lat = radians(std::clamp(latitude, -85.05112878, 85.05112878));
    return {(longitude + 180.0) / 360.0 * count, (1.0 - std::asinh(std::tan(lat)) / pi) * 0.5 * count};
}

std::array<double, 2> tileToGeographic(double x, double y, int zoom) {
    const double count = std::ldexp(1.0, zoom);
    return {degrees(std::atan(std::sinh(pi * (1 - 2 * y / count)))), x / count * 360.0 - 180.0};
}

std::array<double, 3> geographicToEnu(double latitude, double longitude, double altitude, const Config& config) {
    const auto point = ecef(latitude, longitude, altitude);
    const auto origin = ecef(config.originLatitude, config.originLongitude, config.originAltitude);
    const double x = point[0] - origin[0], y = point[1] - origin[1], z = point[2] - origin[2];
    const double lat = radians(config.originLatitude), lon = radians(config.originLongitude);
    return {-std::sin(lon) * x + std::cos(lon) * y,
            -std::sin(lat) * std::cos(lon) * x - std::sin(lat) * std::sin(lon) * y + std::cos(lat) * z,
            std::cos(lat) * std::cos(lon) * x + std::cos(lat) * std::sin(lon) * y + std::sin(lat) * z};
}

double terrariumHeight(std::uint8_t red, std::uint8_t green, std::uint8_t blue) {
    return red * 256.0 + green + blue / 256.0 - 32768.0;
}

std::string expandUrl(const std::string& pattern, const TileKey& key) {
    std::string result = pattern;
    for (const auto& entry : {std::make_pair("{x}", key.x), std::make_pair("{y}", key.y), std::make_pair("{z}", key.zoom)}) {
        std::size_t offset = 0;
        const std::string value = std::to_string(entry.second);
        while ((offset = result.find(entry.first, offset)) != std::string::npos) {
            result.replace(offset, std::strlen(entry.first), value);
            offset += value.size();
        }
    }
    return result;
}

double syntheticElevation(double east, double north, const Config& config) {
    const double distance = std::hypot(std::max(0.0, std::abs(east) - 18.0),
                                      std::max({0.0, -110.0 - north, north - 930.0}));
    double ramp = std::clamp(distance / 110.0, 0.0, 1.0);
    ramp = ramp * ramp * (3 - 2 * ramp);
    const double phase = config.seed * 0.731;
    const double hills = 0.5 + 0.28 * std::sin(east / 170 + phase) * std::cos(north / 210) +
        0.15 * std::sin(east / 71 - north / 113 + phase) + 0.07 * std::cos(north / 37 + east / 97);
    return -0.06 + config.syntheticHeight * ramp * hills;
}

std::vector<TileKey> desiredTiles(double east, double north, const Config& config) {
    if (!std::isfinite(east) || !std::isfinite(north) || std::abs(east) > 10000000 || std::abs(north) > 10000000)
        throw std::invalid_argument("Terrain target position is outside the supported ENU region");
    int centerX, centerY;
    if (config.source == "procedural") {
        centerX = static_cast<int>(std::floor(east / config.tileSize));
        centerY = static_cast<int>(std::floor(north / config.tileSize));
    } else {
        const double latitude = config.originLatitude + degrees(north / radiusEarth);
        const double longitude = config.originLongitude + degrees(east / (radiusEarth * std::cos(radians(config.originLatitude))));
        const auto tile = geographicToTile(latitude, longitude, config.zoom);
        centerX = static_cast<int>(std::floor(tile[0]));
        centerY = static_cast<int>(std::floor(tile[1]));
    }
    std::vector<TileKey> result;
    for (int y = centerY - config.radius; y <= centerY + config.radius; ++y)
        for (int x = centerX - config.radius; x <= centerX + config.radius; ++x) {
            if (config.source == "xyz" && (x < 0 || y < 0 || x >= (1 << config.zoom) || y >= (1 << config.zoom))) continue;
            result.push_back({x, y, config.source == "xyz" ? config.zoom : 0});
        }
    std::stable_sort(result.begin(), result.end(), [&](const TileKey& a, const TileKey& b) {
        return std::hypot(a.x - centerX, a.y - centerY) < std::hypot(b.x - centerX, b.y - centerY);
    });
    return result;
}

TileMesh buildTile(const TileKey& key, const Config& config, const std::atomic_bool* cancelled) {
    config.validate();
    TileMesh result;
    result.key = key;
    const int cells = config.meshCells;
    std::map<TileKey, Image> elevation;
    auto imageFor = [&](TileKey tile) -> const Image& {
        const int count = 1 << tile.zoom;
        tile.x = (tile.x % count + count) % count;
        tile.y = std::clamp(tile.y, 0, count - 1);
        auto existing = elevation.find(tile);
        if (existing != elevation.end()) return existing->second;
        const auto file = obtain(config, config.elevationUrl, tile, "png", cancelled);
        try { return elevation.emplace(tile, readPng(file)).first->second; }
        catch (...) { std::filesystem::remove(file); throw; }
    };
    auto sampleHeight = [&](double u, double v) {
        const auto& primary = imageFor(key);
        const double pixelX = u * primary.width - 0.5, pixelY = v * primary.height - 0.5;
        const int x = static_cast<int>(std::floor(pixelX)), y = static_cast<int>(std::floor(pixelY));
        auto pixel = [&](int px, int py) {
            TileKey tile = key;
            if (px < 0) { --tile.x; px += primary.width; }
            if (py < 0) { --tile.y; py += primary.height; }
            if (px >= static_cast<int>(primary.width)) { ++tile.x; px -= primary.width; }
            if (py >= static_cast<int>(primary.height)) { ++tile.y; py -= primary.height; }
            const auto& neighbor = imageFor(tile);
            if (neighbor.width != primary.width || neighbor.height != primary.height)
                throw std::runtime_error("Elevation tiles must have matching dimensions");
            const std::size_t index = 4 * (static_cast<std::size_t>(py) * neighbor.width + px);
            if (neighbor.pixels[index + 3] == 0) throw std::runtime_error("Elevation tile contains no-data pixels");
            return terrariumHeight(neighbor.pixels[index], neighbor.pixels[index + 1], neighbor.pixels[index + 2]);
        };
        const double fx = pixelX - x, fy = pixelY - y;
        return (1 - fy) * ((1 - fx) * pixel(x, y) + fx * pixel(x + 1, y)) +
               fy * ((1 - fx) * pixel(x, y + 1) + fx * pixel(x + 1, y + 1));
    };
    for (int row = 0; row <= cells; ++row)
        for (int col = 0; col <= cells; ++col) {
            if (cancelled && cancelled->load()) throw std::runtime_error("Terrain request cancelled");
            const double u = static_cast<double>(col) / cells, v = static_cast<double>(row) / cells;
            std::array<double, 3> point;
            if (config.source == "procedural") {
                point = {(key.x + u) * config.tileSize, (key.y + v) * config.tileSize, 0};
                point[2] = syntheticElevation(point[0], point[1], config);
            } else {
                const auto geographic = tileToGeographic(key.x + u, key.y + v, key.zoom);
                point = geographicToEnu(geographic[0], geographic[1], sampleHeight(u, v), config);
            }
            result.points.push_back({static_cast<float>(point[0]), static_cast<float>(point[1]), static_cast<float>(point[2])});
            result.textureCoordinates.push_back({static_cast<float>(u), static_cast<float>(config.source == "xyz" ? 1 - v : v)});
        }
    for (int row = 0; row < cells; ++row)
        for (int col = 0; col < cells; ++col) {
            const int a = row * (cells + 1) + col, b = a + 1, c = a + cells + 1, d = c + 1;
            if (config.source == "xyz") result.indices.insert(result.indices.end(), {a, c, b, b, c, d});
            else result.indices.insert(result.indices.end(), {a, b, c, b, d, c});
        }
    result.textureFile = config.source == "procedural" ? syntheticTexture(key, config) :
        obtain(config, config.imageryUrl, key, config.imageryExtension, cancelled);
    if (config.source == "xyz") {
        try {
            if (config.imageryExtension == "png") readPng(result.textureFile);
            else {
                std::ifstream input(result.textureFile, std::ios::binary);
                unsigned char signature[3]{};
                input.read(reinterpret_cast<char*>(signature), sizeof(signature));
                if (!input || signature[0] != 0xff || signature[1] != 0xd8 || signature[2] != 0xff)
                    throw std::runtime_error("Imagery tile is not a JPEG image");
            }
        } catch (...) { std::filesystem::remove(result.textureFile); throw; }
    }
    return result;
}

Streamer::Streamer(Config config) : config_(std::move(config)) {
    config_.validate();
    worker_ = std::thread([this] { work(); });
}

Streamer::~Streamer() {
    stopping_.store(true);
    condition_.notify_all();
    if (worker_.joinable()) worker_.join();
}

void Streamer::request(const std::vector<TileKey>& desired) {
    std::lock_guard<std::mutex> lock(mutex_);
    desired_ = {desired.begin(), desired.end()};
    for (auto iterator = scheduled_.begin(); iterator != scheduled_.end();)
        if (!desired_.count(*iterator)) iterator = scheduled_.erase(iterator); else ++iterator;
    for (auto iterator = retries_.begin(); iterator != retries_.end();)
        if (!desired_.count(iterator->first)) iterator = retries_.erase(iterator); else ++iterator;
    queue_.erase(std::remove_if(queue_.begin(), queue_.end(), [&](const TileKey& key) { return !desired_.count(key); }), queue_.end());
    for (const auto& key : desired)
        if (!scheduled_.count(key) && !retries_.count(key)) { queue_.push_back(key); scheduled_.insert(key); }
    condition_.notify_one();
}

std::vector<TileResult> Streamer::collect() {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<TileResult> result;
    while (!ready_.empty()) {
        if (desired_.count(ready_.front().key)) result.push_back(std::move(ready_.front()));
        ready_.pop_front();
    }
    return result;
}

void Streamer::work() {
    while (!stopping_.load()) {
        TileKey key;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            condition_.wait_for(lock, std::chrono::seconds(1), [&] { return stopping_.load() || !queue_.empty(); });
            if (stopping_.load()) return;
            const auto now = std::chrono::steady_clock::now();
            for (auto iterator = retries_.begin(); iterator != retries_.end();)
                if (iterator->second <= now) {
                    if (desired_.count(iterator->first)) { queue_.push_back(iterator->first); scheduled_.insert(iterator->first); }
                    iterator = retries_.erase(iterator);
                } else ++iterator;
            if (queue_.empty()) continue;
            key = queue_.front();
            queue_.pop_front();
        }
        TileResult result;
        result.key = key;
        try { result.mesh = buildTile(key, config_, &stopping_); }
        catch (const std::exception& error) { result.error = error.what(); }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (desired_.count(key)) {
                if (!result.error.empty()) {
                    scheduled_.erase(key);
                    retries_[key] = std::chrono::steady_clock::now() + std::chrono::seconds(30);
                }
                ready_.push_back(std::move(result));
            }
        }
    }
}

}
