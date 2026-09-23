#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <sys/types.h>

namespace px4isaac {
struct StartupState {
    bool ready = false;
    bool disconnected = false;
    bool listening = false;
};
StartupState startup_state(pid_t pid, std::uint16_t simulator_port, std::uint16_t mavlink_port);
void prepare_startup(const std::string& source, const std::string& startup_path, const std::string& mavlink_path,
                     unsigned instance, bool router, bool check_ports = true);
int run_process(const std::vector<std::string>& command, std::uint16_t simulator_port, std::uint16_t mavlink_port);
}
