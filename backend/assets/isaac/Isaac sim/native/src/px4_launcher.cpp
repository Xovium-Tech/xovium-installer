#include "px4_launcher.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <system_error>
#include <thread>

#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

namespace px4isaac {
namespace {
volatile std::sig_atomic_t interrupted = 0;
void interrupt_handler(int number) { interrupted = number; }

std::vector<std::string> read_lines(const std::filesystem::path& path) {
    std::ifstream stream(path);
    if (!stream) throw std::runtime_error("Cannot read " + path.string());
    std::vector<std::string> result;
    std::string line;
    while (std::getline(stream, line)) result.push_back(line);
    if (stream.bad()) throw std::runtime_error("Cannot finish reading " + path.string());
    return result;
}

void write_lines(const std::filesystem::path& path, const std::vector<std::string>& lines) {
    const auto temporary = path.string() + ".tmp-" + std::to_string(getpid());
    try {
        std::ofstream stream(temporary, std::ios::trunc);
        if (!stream) throw std::runtime_error("Cannot write " + temporary);
        for (const auto& line : lines) stream << line << '\n';
        stream.close();
        if (!stream) throw std::runtime_error("Cannot finish writing " + temporary);
        std::filesystem::rename(temporary, path);
    } catch (...) {
        std::error_code ignored;
        std::filesystem::remove(temporary, ignored);
        throw;
    }
}

bool comment(const std::string& line) {
    const auto first = line.find_first_not_of(" \t\r");
    return first != std::string::npos && line[first] == '#';
}

void stop_child(pid_t child, bool& reaped, int& status) {
    if (reaped) return;
    const auto result = waitpid(child, &status, WNOHANG);
    if (result == child || (result < 0 && errno == ECHILD)) { reaped = true; return; }
    kill(child, SIGTERM);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (std::chrono::steady_clock::now() < deadline) {
        const auto waited = waitpid(child, &status, WNOHANG);
        if (waited == child || (waited < 0 && errno == ECHILD)) { reaped = true; return; }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    kill(child, SIGKILL);
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
    reaped = true;
}

[[maybe_unused]] unsigned parse_instance(const std::string& value) {
    if (value.empty() || (value.size() > 1 && value[0] == '0') || value.size() > 3
        || value.find_first_not_of("0123456789") != std::string::npos)
        throw std::invalid_argument("PX4 instance must be an integer from 0 to 254");
    const auto number = static_cast<unsigned>(std::stoul(value));
    if (number > 254) throw std::invalid_argument("PX4 instance must be an integer from 0 to 254");
    return number;
}
}

StartupState startup_state(pid_t pid, std::uint16_t simulator_port, std::uint16_t mavlink_port) {
    const auto process = std::filesystem::path("/proc") / std::to_string(pid);
    std::set<std::string> sockets;
    for (const auto& descriptor : std::filesystem::directory_iterator(process / "fd")) {
        std::error_code error;
        const auto target = std::filesystem::read_symlink(descriptor.path(), error).string();
        if (error == std::errc::no_such_file_or_directory) continue;
        if (error) throw std::system_error(error, "Cannot inspect child socket");
        if (target.rfind("socket:[", 0) == 0 && target.back() == ']') sockets.insert(target.substr(8, target.size() - 9));
    }
    StartupState result;
    for (const std::string protocol : {"udp", "udp6", "tcp", "tcp6"}) {
        const auto lines = read_lines(process / "net" / protocol);
        for (std::size_t i = 1; i < lines.size(); ++i) {
            std::istringstream row(lines[i]);
            std::array<std::string, 10> fields;
            for (auto& field : fields) row >> field;
            if (!row) continue;
            const auto local_port = std::stoul(fields[1].substr(fields[1].rfind(':') + 1), nullptr, 16);
            const auto remote_port = std::stoul(fields[2].substr(fields[2].rfind(':') + 1), nullptr, 16);
            const bool tcp = protocol.rfind("tcp", 0) == 0;
            if (tcp && local_port == simulator_port && fields[3] == "0A") result.listening = true;
            if (!sockets.count(fields[9])) continue;
            if (!tcp && local_port == mavlink_port) result.ready = true;
            if (tcp && remote_port == simulator_port && fields[3] == "08") result.disconnected = true;
        }
    }
    return result;
}

void prepare_startup(const std::string& source, const std::string& startup_path, const std::string& mavlink_path,
                     unsigned instance, bool router, bool check_ports) {
    if (instance > 254) throw std::invalid_argument("PX4 instance must be from 0 to 254");
    const auto startup = read_lines(std::filesystem::path(source) / "rcS");
    const auto mavlink = read_lines(std::filesystem::path(source) / "px4-rc.mavlink");
    const std::string dds_start = "uxrce_dds_client start -t udp -h 127.0.0.1 -p $uxrce_dds_port $uxrce_dds_ns";
    const std::string mavlink_source = ". px4-rc.mavlink";
    const std::string airframe = "\t. \"$autostart_file\"";
    const std::string offboard = "mavlink start -x -u $udp_offboard_port_local -r 4000000 -f -m onboard -o $udp_offboard_port_remote";
    for (const auto& expected : {dds_start, mavlink_source, airframe}) {
        if (std::count(startup.begin(), startup.end(), expected) != 1)
            throw std::runtime_error("Unsupported PX4 startup layout; expected exactly one line: " + expected);
    }
    if (std::count(mavlink.begin(), mavlink.end(), offboard) != 1)
        throw std::runtime_error("Unsupported PX4 startup layout; expected exactly one line: " + offboard);
    if (check_ports) {
        std::vector<std::pair<std::string, unsigned>> ports{{"QGC/router", 18570}, {"payload", 14280}, {"gimbal", 13030}};
        if (!router) ports.emplace_back("MAVLink application", 14580);
        std::vector<std::string> conflicts;
        for (const auto& [name, base] : ports) {
            const unsigned port = base + instance;
            const int connection = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
            if (connection < 0) throw std::system_error(errno, std::generic_category(), "Cannot check UDP " + std::to_string(port));
            sockaddr_in address{};
            address.sin_family = AF_INET;
            address.sin_port = htons(static_cast<std::uint16_t>(port));
            address.sin_addr.s_addr = INADDR_ANY;
            const int result = bind(connection, reinterpret_cast<const sockaddr*>(&address), sizeof(address));
            const int saved_errno = errno;
            close(connection);
            if (result != 0) {
                if (saved_errno != EADDRINUSE)
                    throw std::system_error(saved_errno, std::generic_category(), "Cannot check UDP " + std::to_string(port));
                conflicts.push_back(name + ": UDP " + std::to_string(port));
            }
        }
        if (!conflicts.empty()) {
            std::string message = "PX4 cannot start because these local ports are already in use: ";
            for (std::size_t i = 0; i < conflicts.size(); ++i) message += (i ? ", " : "") + conflicts[i];
            message += ". Close any previous PX4 instance before restarting.";
            if (!router) message += " If a MAVLink router owns the application port and forwards PX4 UDP 14550, launch with PX4_MAVLINK_ROUTER=1 ./run_px4.sh.";
            throw std::runtime_error(message);
        }
    }
    std::vector<std::string> startup_output, mavlink_output;
    for (const auto& line : startup) {
        if (comment(line) || line == dds_start) continue;
        startup_output.push_back(line == mavlink_source ? ". \"$PX4_MAVLINK_STARTUP\"" : line);
        if (line == airframe) startup_output.emplace_back("\t. \"$PX4_ISAAC_AIRFRAME\"");
    }
    for (const auto& line : mavlink) {
        if (!comment(line) && !(router && line == offboard)) mavlink_output.push_back(line);
    }
    write_lines(startup_path, startup_output);
    write_lines(mavlink_path, mavlink_output);
}

int run_process(const std::vector<std::string>& command, std::uint16_t simulator_port, std::uint16_t mavlink_port) {
    if (command.empty()) throw std::invalid_argument("PX4 executable is required");
    bool simulator_seen = startup_state(getpid(), simulator_port, mavlink_port).listening;
    std::vector<char*> arguments;
    for (const auto& value : command) arguments.push_back(const_cast<char*>(value.c_str()));
    arguments.push_back(nullptr);
    const pid_t child = fork();
    if (child < 0) throw std::system_error(errno, std::generic_category(), "Cannot fork PX4");
    if (child == 0) {
        execvp(arguments[0], arguments.data());
        const std::string message = std::string("Cannot start PX4: ") + std::strerror(errno) + "\n";
        const auto written = write(STDERR_FILENO, message.data(), message.size());
        static_cast<void>(written);
        _exit(127);
    }
    interrupted = 0;
    const auto old_int = std::signal(SIGINT, interrupt_handler);
    const auto old_term = std::signal(SIGTERM, interrupt_handler);
    bool guarding = true, reaped = false;
    int status = 0, result = 1;
    try {
        while (true) {
            const auto waited = waitpid(child, &status, WNOHANG);
            if (waited == child) {
                reaped = true;
                result = WIFEXITED(status) ? WEXITSTATUS(status) : (WIFSIGNALED(status) ? 128 + WTERMSIG(status) : 1);
                break;
            }
            if (waited < 0 && errno != EINTR) throw std::system_error(errno, std::generic_category(), "Cannot wait for PX4");
            if (interrupted != 0) {
                kill(child, interrupted);
                result = 128 + interrupted;
                break;
            }
            if (guarding) {
                try {
                    const auto state = startup_state(child, simulator_port, mavlink_port);
                    if (state.ready) guarding = false;
                    else if (state.disconnected || (simulator_seen && !state.listening)) {
                        std::cerr << "Isaac disconnected from TCP " << simulator_port << " before PX4 finished startup. "
                            "PX4 cannot finish startup or open its QGC connection. "
                            "Check the Isaac terminal error, restart Isaac, then run ./run_px4.sh again.\n";
                        result = 1;
                        break;
                    }
                    simulator_seen = simulator_seen || state.listening;
                } catch (const std::exception& error) {
                    const auto alive = waitpid(child, &status, WNOHANG);
                    if (alive == child) {
                        reaped = true;
                        result = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
                        break;
                    }
                    std::cerr << "Cannot monitor PX4 simulator startup: " << error.what() << '\n';
                    guarding = false;
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
        }
    } catch (...) {
        stop_child(child, reaped, status);
        std::signal(SIGINT, old_int);
        std::signal(SIGTERM, old_term);
        throw;
    }
    stop_child(child, reaped, status);
    std::signal(SIGINT, old_int);
    std::signal(SIGTERM, old_term);
    return result;
}
}

#ifndef PX4_ISAAC_LAUNCHER_LIBRARY
int main(int argc, char** argv) {
    try {
        if (argc == 7 && std::string(argv[1]) == "prepare") {
            const unsigned instance = px4isaac::parse_instance(argv[5]);
            if (std::string(argv[6]) != "0" && std::string(argv[6]) != "1") throw std::invalid_argument("Router mode must be 0 or 1");
            px4isaac::prepare_startup(argv[2], argv[3], argv[4], instance, std::string(argv[6]) == "1");
            return 0;
        }
        if (argc >= 4 && std::string(argv[1]) == "run") {
            const unsigned instance = px4isaac::parse_instance(argv[2]);
            return px4isaac::run_process(std::vector<std::string>(argv + 3, argv + argc), 4560 + instance, 18570 + instance);
        }
        throw std::invalid_argument("Usage: px4_launcher prepare SOURCE RCS MAVLINK INSTANCE ROUTER | px4_launcher run INSTANCE COMMAND [ARG ...]");
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
#endif
