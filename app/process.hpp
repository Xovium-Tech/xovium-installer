#pragma once
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>
#include <sys/types.h>

class Process {
public:
    Process() = default;
    Process(const Process&) = delete;
    Process& operator=(const Process&) = delete;
    ~Process();
    bool start(const std::vector<std::string>& arguments, const std::filesystem::path& logfile = {}, bool separateStderr = false);
    void poll();
    void cancel();
    bool running() const { return active_; }
    int status = -1;
    std::string output, diagnostics, error;
    uint64_t outputRevision = 0;
    bool stopping = false;
private:
    pid_t pid_ = -1, group_ = -1;
    int pipe_ = -1, errorPipe_ = -1, exitStatus_ = -1;
    bool active_ = false;
    std::ofstream log_;
};
void openUrl(const std::string& url);
std::string jsonString(const std::string& text);
std::string pickerPath(const std::string& output);
