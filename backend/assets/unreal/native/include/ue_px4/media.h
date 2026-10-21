#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Plain C boundary: Unreal uses libc++, distro GStreamer/OpenCV use libstdc++.
 * Owned handles must be destroyed before module unload. Callbacks borrow data
 * only for their duration and always execute on the caller of terrain_poll. */
typedef struct {
    double latitude, longitude, altitude, radius_m;
    int zoom, cells;
    const char* cache;
    const char* imagery_url;
    const char* elevation_url;
} xov_terrain_config;
typedef struct {
    int texture_size; /* Per-page limit, power of two: 256..4096. */
    int imagery_zoom; /* Desired near-field source zoom: geometry zoom..20. */
    double detail_radius_m;
} xov_terrain_quality;
typedef struct xov_terrain_page {
    int section, reset;
    size_t vertices, indices;
    const double* positions; /* north/east/up metres */
    const double* normals;
    const double* uv;
    const uint32_t* triangles;
    int width, height;
    const uint8_t* rgb;
    int texture_only; /* Update an existing section; mesh pointers are null. */
    int imagery_zoom; /* Actual source detail, not the upscaled texture width. */
} xov_terrain_page;
typedef void (*xov_terrain_callback)(void* user, const xov_terrain_page* page);
/* Legacy callers retain the original small preview texture budget. */
void* xov_terrain_create(const xov_terrain_config* config);
void* xov_terrain_create_with_quality(const xov_terrain_config* config, const xov_terrain_quality* quality);
void xov_terrain_update(void* terrain, double north, double east, double up);
int xov_terrain_poll(void* terrain, xov_terrain_callback callback, void* user);
void xov_terrain_destroy(void* terrain);
#ifdef __cplusplus
}
#endif
