#include "process.hpp"
#include <cerrno>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>
extern char** environ;

Process::~Process() {
    if (running()) cancel();
    if (pipe_ >= 0) close(pipe_);
    if (errorPipe_ >= 0) close(errorPipe_);
}
bool Process::start(const std::vector<std::string>& arguments, const std::filesystem::path& logfile, bool separateStderr) {
    if (running() || arguments.empty()) return false;
    if (log_.is_open()) log_.close();
    output.clear(); diagnostics.clear(); error.clear(); outputRevision = 0;
    status = -1; exitStatus_ = -1; stopping = false;
    if (!logfile.empty()) {
        log_.open(logfile, std::ios::out | std::ios::trunc);
        if (!log_) { error = "Cannot create log: " + logfile.string(); return false; }
    }
    int pipes[2], errors[2] = {-1, -1};
    if (pipe2(pipes, O_CLOEXEC) != 0) { error = strerror(errno); return false; }
    if (separateStderr && pipe2(errors, O_CLOEXEC) != 0) {
        error = strerror(errno); close(pipes[0]); close(pipes[1]); return false;
    }
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, pipes[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&actions, separateStderr ? errors[1] : pipes[1], STDERR_FILENO);
    for (int fd : {pipes[0], pipes[1], errors[0], errors[1]})
        if (fd >= 0) posix_spawn_file_actions_addclose(&actions, fd);
    posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attr, 0);
    std::vector<char*> argv;
    for (const auto& argument : arguments) argv.push_back(const_cast<char*>(argument.c_str()));
    argv.push_back(nullptr);
    const int result = posix_spawnp(&pid_, argv[0], &actions, &attr, argv.data(), environ);
    posix_spawnattr_destroy(&attr);
    posix_spawn_file_actions_destroy(&actions);
    close(pipes[1]);
    if (errors[1] >= 0) close(errors[1]);
    if (result != 0) {
        close(pipes[0]); if (errors[0] >= 0) close(errors[0]);
        pid_ = -1; error = strerror(result); return false;
    }
    pipe_ = pipes[0]; errorPipe_ = errors[0]; group_ = pid_; active_ = true;
    for (int fd : {pipe_, errorPipe_})
        if (fd >= 0) fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    return true;
}
void Process::poll() {
    auto drain = [&](int& fd, std::string& text, bool isOutput) {
        if (fd < 0) return;
        char buffer[8192];
        for (int i = 0; i < 64; ++i) {
            const auto count = read(fd, buffer, sizeof(buffer));
            if (count > 0) {
                text.append(buffer, static_cast<size_t>(count));
                if (isOutput) ++outputRevision;
                if (log_.is_open()) log_.write(buffer, count);
            } else if (count == 0) { close(fd); fd = -1; break; }
            else if (errno == EINTR) continue;
            else if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            else { error = strerror(errno); close(fd); fd = -1; break; }
        }
        if (text.size() > 250000) text.erase(0, text.size() - 220000);
    };
    drain(pipe_, output, true); drain(errorPipe_, diagnostics, false);
    if (log_.is_open()) log_.flush();
    if (pid_ > 0) {
        int result = 0;
        if (waitpid(pid_, &result, WNOHANG) == pid_) {
            exitStatus_ = WIFEXITED(result) ? WEXITSTATUS(result) : 128 + WTERMSIG(result);
            pid_ = -1;
        }
    }
    if (active_ && pid_ < 0 && pipe_ < 0 && errorPipe_ < 0) {
        active_ = false; group_ = -1; status = exitStatus_;
        if (log_.is_open()) log_.close();
    }
}
void Process::cancel() {
    if (running() && group_ > 0 && !stopping) { stopping = true; kill(-group_, SIGINT); }
}
std::string pickerPath(const std::string& output) {
    std::string path = output;
    while (!path.empty() && (path.back() == '\n' || path.back() == '\r')) path.pop_back();
    if (path.empty() || path.find_first_of("\r\n") != std::string::npos || path.find('\0') != std::string::npos || !std::filesystem::path(path).is_absolute())
        throw std::runtime_error("The file chooser did not return a single local path. Type the path in the field or try Browse again.");
    return path;
}
void openUrl(const std::string& url) {
    const pid_t child = fork();
    if (child == 0) {
        const pid_t grandchild = fork();
        if (grandchild == 0) { setsid(); execlp("xdg-open", "xdg-open", url.c_str(), nullptr); _exit(127); }
        _exit(grandchild < 0 ? 1 : 0);
    }
    if (child > 0) { int status; while (waitpid(child, &status, 0) < 0 && errno == EINTR) {} }
}
std::string jsonString(const std::string& text) {
    std::string result = "\"";
    static const char hex[] = "0123456789abcdef";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') { result += '\\'; result += c; }
        else if (c < 32) { result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15]; }
        else result += c;
    }
    return result + '"';
}
