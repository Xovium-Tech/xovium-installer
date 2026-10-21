#pragma once
#include <opencv2/core/mat.hpp>
#include <string>
namespace dynamic_terrain {
// Decode JPEG through a private C library: Unreal exports an incompatible JPEG ABI.
cv::Mat loadTerrainImage(const std::string& path);
}
