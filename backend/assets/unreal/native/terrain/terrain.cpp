#include "ue_px4/media.h"
#include "dynamic_terrain/core/TerrainRuntime.hh"
#include <deque>
#include <mutex>
#include <cmath>
#include <stdexcept>
namespace dt = dynamic_terrain;
namespace {
struct Geographic final : dt::GeographicTransform {
    double lat, lon, alt;
    explicit Geographic(const xov_terrain_config& c):lat(c.latitude),lon(c.longitude),alt(c.altitude) {}
    dt::Vec3 LocalFromGeodetic(double a,double b,double h) const override {
        constexpr double meters=6378137.0*dt::kPi/180;
        return {(a-lat)*meters,(b-lon)*meters*std::cos(lat*dt::kPi/180),h-alt};
    }
    dt::Vec3 GeodeticFromLocal(const dt::Vec3& p) const override {
        constexpr double meters=6378137.0*dt::kPi/180;
        return {lat+p.X()/meters,lon+p.Y()/(meters*std::cos(lat*dt::kPi/180)),alt+p.Z()};
    }
    double LatitudeDeg() const override {return lat;}
    double LongitudeDeg() const override {return lon;}
    double ElevationReference() const override {return alt;}
};
struct Terrain final : dt::TerrainRenderSink {
    std::mutex mutex;
    std::shared_ptr<const dt::TerrainSnapshot> pending, active;
    std::deque<dt::TextureUpdate> textures;
    std::shared_ptr<Geographic> geo;
    std::unique_ptr<dt::TerrainRuntime> runtime; // destroyed first, joins workers before sink dies
    Terrain(const xov_terrain_config& c,const xov_terrain_quality& quality) : geo(std::make_shared<Geographic>(c)) {
        dt::Config cfg;
        cfg.modelName="unreal";
        cfg.cacheDir=c.cache && *c.cache ? c.cache : "~/.cache/gz_dynamic_terrain";
        cfg.visualRadiusM=std::clamp(c.radius_m,100.0,15000.0);
        cfg.visualGeometryZoom=std::clamp(c.zoom,10,17);
        cfg.visualElevationZoom=cfg.visualGeometryZoom;
        cfg.visualMeshCellsPerTile=std::clamp(c.cells,8,128);
        cfg.visualRecenterDistanceM=cfg.visualRadiusM*.4;
        cfg.visualBootstrapImageryZoom=std::min(12,cfg.visualGeometryZoom-1);
        const int requested=std::clamp(quality.texture_size,256,4096);
        int textureSize=256;
        while(textureSize*2<=requested) textureSize*=2;
        cfg.visualPageTextureMaxSize=textureSize;
        cfg.visualTextureSize=textureSize;
        cfg.visualDetailZoom=std::clamp(quality.imagery_zoom,cfg.visualGeometryZoom,20);
        cfg.visualDetailRadiusM=std::clamp(quality.detail_radius_m,100.0,15000.0);
        const auto radius=cfg.visualDetailRadiusM;
        const int detail=cfg.visualDetailZoom;
        cfg.visualImageryLodTable={{radius,detail},{radius*2.5,detail-1},
                                  {radius*5.5,detail-2},{1000000.0,cfg.visualGeometryZoom}};
        cfg.visualRefineTexture=true;
        cfg.visualProgressiveUploads=true;
        cfg.enableCollision=false; // Unreal cooks the visual triangles into Chaos collision.
        cfg.imageryProvider="google_satellite";
        if(c.imagery_url && *c.imagery_url) {cfg.imageryProvider="custom"; cfg.imageryUrl=c.imagery_url;}
        if(c.elevation_url && *c.elevation_url) cfg.elevationUrl=c.elevation_url;
        cfg.diagnostics=false;
        dt::normalizeConfig(cfg);
        runtime=std::make_unique<dt::TerrainRuntime>(cfg,geo,*this);
    }
    void QueueSnapshot(std::shared_ptr<const dt::TerrainSnapshot> s) override {
        std::lock_guard<std::mutex> lock(mutex); pending=std::move(s); textures.clear();
    }
    void QueueTexture(dt::TextureUpdate t) override {
        std::lock_guard<std::mutex> lock(mutex);
        if(textures.size()>=8) textures.pop_front();
        textures.push_back(std::move(t));
    }
    uint64_t ActiveGeneration() const override {auto* self=const_cast<Terrain*>(this); std::lock_guard<std::mutex> lock(self->mutex);return active?active->generation:0;}
    bool HasActiveTerrain() const override {return ActiveGeneration()!=0;}
    std::optional<dt::TileKey> ActiveCenterTile() const override {
        auto* self=const_cast<Terrain*>(this);std::lock_guard<std::mutex> lock(self->mutex);
        return active?std::optional<dt::TileKey>(active->centerTile):std::nullopt;
    }
};
}
extern "C" {
void* xov_terrain_create(const xov_terrain_config* c) {
    const xov_terrain_quality preview{512,17,1000};
    return xov_terrain_create_with_quality(c,&preview);
}
void* xov_terrain_create_with_quality(const xov_terrain_config* c,const xov_terrain_quality* q) {
    const xov_terrain_quality defaults{2048,17,1000};
    const auto& quality=q?*q:defaults;
    if(!std::isfinite(quality.detail_radius_m)) return nullptr;
    if(!c||!std::isfinite(c->latitude)||std::abs(c->latitude)>85||!std::isfinite(c->longitude)||std::abs(c->longitude)>180
       ||!std::isfinite(c->altitude)||!std::isfinite(c->radius_m)) return nullptr;
    try {return new Terrain(*c,quality);} catch(...) {return nullptr;}
}
void xov_terrain_update(void* p,double n,double e,double u) {
    if(!p||!std::isfinite(n)||!std::isfinite(e)||!std::isfinite(u)) return;
    try {auto& t=*static_cast<Terrain*>(p);const dt::Vec3 pos(n,e,u);auto ll=t.geo->GeodeticFromLocal(pos);
        t.runtime->UpdateVisual(pos,ll.X(),ll.Y());} catch(...) {}
}
int xov_terrain_poll(void* p,xov_terrain_callback cb,void* user) {
    if(!p||!cb) return -1;
    try {
        auto& t=*static_cast<Terrain*>(p);
        std::shared_ptr<const dt::TerrainSnapshot> snapshot;
        std::deque<dt::TextureUpdate> updates;
        uint64_t generation=0;
        {std::lock_guard<std::mutex> lock(t.mutex);
            snapshot=std::move(t.pending); if(snapshot)t.active=snapshot;
            updates.swap(t.textures); generation=t.active?t.active->generation:0;
        }
        int delivered=0;
        bool reset=true;
        if(snapshot) for(const auto& page:snapshot->pages) {
            if(!page.mesh) continue;
            std::vector<double> pos,normal,uv;
            for(auto v:page.mesh->positions) {pos.insert(pos.end(),{v.X(),v.Y(),v.Z()});}
            for(auto v:page.mesh->normals) {normal.insert(normal.end(),{v.X(),v.Y(),v.Z()});}
            for(auto v:page.mesh->texCoords) {uv.insert(uv.end(),{v.X(),v.Y()});}
            const auto tex=page.texture;
            xov_terrain_page data{int(page.index),reset,pos.size()/3,page.mesh->indices.size(),
                pos.data(),normal.data(),uv.data(),page.mesh->indices.data(),
                tex?int(tex->width):0,tex?int(tex->height):0,tex?tex->rgb.data():nullptr,0,page.imageryZoom};
            cb(user,&data);reset=false;++delivered;
        }
        // Refinement is asynchronous. Deliver imagery without clearing/re-cooking
        // the mesh, and never apply updates belonging to an older ground patch.
        for(const auto& update:updates) {
            if(update.generation!=generation) continue;
            for(const auto& page:update.pages) {
                if(!page.texture || !page.texture->Valid()) continue;
                const auto& tex=*page.texture;
                xov_terrain_page data{int(page.pageIndex),0,0,0,nullptr,nullptr,nullptr,nullptr,
                    int(tex.width),int(tex.height),tex.rgb.data(),1,page.imageryZoom};
                cb(user,&data);++delivered;
            }
        }
        return delivered;
    } catch(...) {return -1;}
}
void xov_terrain_destroy(void* p) {delete static_cast<Terrain*>(p);}
}
