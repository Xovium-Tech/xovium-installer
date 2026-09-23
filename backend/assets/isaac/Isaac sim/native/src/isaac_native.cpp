#include <carb/ClientUtils.h>
#include <omni/kit/IApp.h>
#include "IFlight.hpp"
#include <chrono>
#include <thread>
#include <cstdio>
#include <string>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <cstdlib>
#include <filesystem>
#include <sys/file.h>
#include <fcntl.h>
CARB_GLOBALS("px4.isaac.native");
int main(int argc, char** argv) {
    const char* value = std::getenv("PX4_INSTANCE");
    int instance = value ? std::stoi(value) : 0;
    auto lock_directory = std::getenv("PX4_ISAAC_LOCK_DIR");
    std::filesystem::path locks = lock_directory ? lock_directory : "/tmp/px4-isaac-native";
    std::filesystem::create_directories(locks);
    auto lock_path = locks/("isaac-"+std::to_string(instance)+".lock");
    int lock_fd = open(lock_path.c_str(),O_CREAT|O_RDWR|O_CLOEXEC,0600);
    if (lock_fd < 0 || flock(lock_fd,LOCK_EX|LOCK_NB)<0) {
        std::fprintf(stderr,"Isaac instance %d is already starting or running.\n",instance);
        return 1;
    }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    int reuse = 1;
    if (fd >= 0) setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&reuse,sizeof(reuse));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(4560 + instance);
    if (fd < 0 || bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        std::fprintf(stderr, "TCP %d is already in use or unavailable. Close the previous Isaac instance.\n", 4560 + instance);
        if (fd >= 0) close(fd);
        return 1;
    }
    close(fd);
    auto* framework = carb::acquireFrameworkAndRegisterBuiltins();
    std::string plugins = std::string(ISAAC_KIT_PATH) + "/kernel/plugins";
    const char* search[] = { plugins.c_str() };
    const char* wildcards[] = { "omni.kit.app.plugin" };
    auto loading = carb::PluginLoadingDesc::getDefault();
    loading.searchPaths = search;
    loading.searchPathCount = 1;
    loading.loadedFileWildcards = wildcards;
    loading.loadedFileWildcardCount = 1;
    framework->loadPlugins(loading);
    auto* app = framework->tryAcquireInterface<omni::kit::IApp>();
    if (!app) {
        std::fprintf(stderr, "Cannot load the native Kit application interface.\n");
        return 1;
    }
    app->startup({"px4.isaac", ISAAC_KIT_PATH, argc, argv});
    auto next_render = std::chrono::steady_clock::now();
    while (app->isRunning()) {
        if (auto* flight = framework->tryAcquireExistingInterface<px4isaac::IFlight>()) flight->tick();
        auto now = std::chrono::steady_clock::now();
        if (now >= next_render) {
            app->update();
            next_render = std::chrono::steady_clock::now() + std::chrono::milliseconds(16);
        } else {
            std::this_thread::sleep_for(std::chrono::microseconds(150));
        }
    }
    int result = app->shutdown();
    carb::releaseFrameworkAndDeregisterBuiltins();
    return result;
}
