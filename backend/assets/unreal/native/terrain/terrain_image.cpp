#include "ue_px4/terrain_image.hpp"
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <cstdio>
#include <jpeglib.h>
#include <csetjmp>
#include <dlfcn.h>
#include <link.h>
#include <fstream>
#include <iterator>
#include <memory>
#include <vector>

namespace dynamic_terrain {
namespace {
struct JpegLibrary {
    // Isolate only libjpeg's C ABI. Isolating all OpenCV/GStreamer dependencies
    // would interfere with their process-wide allocators and plugin registries.
    void* handle=dlmopen(LM_ID_NEWLM,XOVIUM_JPEG_LIBRARY,RTLD_NOW|RTLD_LOCAL);
    decltype(&jpeg_std_error) error=nullptr;
    decltype(&jpeg_CreateDecompress) create=nullptr;
    decltype(&jpeg_mem_src) source=nullptr;
    decltype(&jpeg_read_header) header=nullptr;
    decltype(&jpeg_start_decompress) start=nullptr;
    decltype(&jpeg_read_scanlines) read=nullptr;
    decltype(&jpeg_finish_decompress) finish=nullptr;
    decltype(&jpeg_destroy_decompress) destroy=nullptr;
    bool ready=false;
    template<typename T> bool bind(T& fn,const char* name) {
        fn=reinterpret_cast<T>(dlsym(handle,name));return fn!=nullptr;
    }
    JpegLibrary() {
        if(handle) ready=bind(error,"jpeg_std_error") && bind(create,"jpeg_CreateDecompress") &&
            bind(source,"jpeg_mem_src") && bind(header,"jpeg_read_header") &&
            bind(start,"jpeg_start_decompress") && bind(read,"jpeg_read_scanlines") &&
            bind(finish,"jpeg_finish_decompress") && bind(destroy,"jpeg_destroy_decompress");
        if(!ready) std::fprintf(stderr,"[DynamicTerrain][JPEG] Cannot load private decoder: %s\n",dlerror());
    }
    // A single process-lifetime namespace; no callbacks survive in unloaded code.
};
struct JpegError {jpeg_error_mgr manager;std::jmp_buf jump;};
struct Decode {
    jpeg_decompress_struct info{};
    JpegError error{};
    std::vector<unsigned char> pixels;
};
void jpegError(j_common_ptr common) {
    auto* error=reinterpret_cast<JpegError*>(common->err);
    std::longjmp(error->jump,1);
}
}
cv::Mat loadTerrainImage(const std::string& path) {
    std::ifstream file(path,std::ios::binary);
    unsigned char signature[2]{};
    file.read(reinterpret_cast<char*>(signature),2);
    if(file.gcount()!=2) return {};
    if(signature[0]!=0xff || signature[1]!=0xd8)
        return cv::imread(path,cv::IMREAD_COLOR);
    file.seekg(0);
    std::vector<unsigned char> encoded((std::istreambuf_iterator<char>(file)),{});
    static JpegLibrary library;
    if(!library.ready) return {};
    // Decoder state is on the heap so a libjpeg error cannot bypass destructors
    // or make modified stack variables indeterminate when it longjmps back.
    auto state=std::make_unique<Decode>();
    state->info.err=library.error(&state->error.manager);
    state->error.manager.error_exit=jpegError;
    if(setjmp(state->error.jump)) {
        char message[JMSG_LENGTH_MAX]{};
        state->error.manager.format_message(reinterpret_cast<j_common_ptr>(&state->info),message);
        std::fprintf(stderr,"[DynamicTerrain][JPEG] Decode failed: %s: %s\n",path.c_str(),message);
        if(state->info.mem) library.destroy(&state->info);
        return {};
    }
    library.create(&state->info,JPEG_LIB_VERSION,sizeof(state->info));
    library.source(&state->info,encoded.data(),encoded.size());
    library.header(&state->info,TRUE);
    state->info.out_color_space=JCS_RGB;
    if(!state->info.image_width || !state->info.image_height || state->info.image_width>8192 || state->info.image_height>8192) {
        library.destroy(&state->info);return {};
    }
    library.start(&state->info);
    const int width=int(state->info.output_width),height=int(state->info.output_height);
    if(state->info.output_components!=3) {library.destroy(&state->info);return {};}
    state->pixels.resize(size_t(width)*height*3);
    while(state->info.output_scanline<state->info.output_height) {
        unsigned char* row=state->pixels.data()+size_t(state->info.output_scanline)*width*3;
        if(library.read(&state->info,&row,1)!=1) {library.destroy(&state->info);return {};}
    }
    library.finish(&state->info);library.destroy(&state->info);
    cv::Mat output;
    cv::cvtColor(cv::Mat(height,width,CV_8UC3,state->pixels.data()),output,cv::COLOR_RGB2BGR);
    return output;
}
}
