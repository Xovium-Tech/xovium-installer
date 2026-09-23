#include "auth.hpp"
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

void wipeSecret(void* data, size_t size) {
    volatile unsigned char* p = static_cast<volatile unsigned char*>(data);
    while (size--) *p++ = 0;
}
namespace {
void disableCoreDumps() {
    const rlimit limit{0, 0};
    setrlimit(RLIMIT_CORE, &limit);
}
bool sameUser(int fd, pid_t expected = -1) {
    ucred peer{}; socklen_t size = sizeof(peer);
    return getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &size) == 0 &&
           peer.uid == getuid() && (expected < 0 || peer.pid == expected);
}
bool sendPacket(int fd, const void* data, size_t size) {
    ssize_t result;
    do { result = send(fd, data, size, MSG_NOSIGNAL); } while (result < 0 && errno == EINTR);
    return result == static_cast<ssize_t>(size);
}
}
Authentication::~Authentication() {
    cancel();
    if (listener_ >= 0) close(listener_);
    if (!endpoint_.empty()) unlink(endpoint_.c_str());
    if (!directory_.empty()) rmdir(directory_.c_str());
}
void Authentication::start() {
    if (listener_ >= 0) return;
    disableCoreDumps();
    char path[] = "/tmp/xovium-auth-XXXXXX";
    if (!mkdtemp(path)) throw std::runtime_error("Cannot create the private password channel.");
    directory_ = path; endpoint_ = directory_ + "/askpass.sock";
    listener_ = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    std::snprintf(address.sun_path, sizeof(address.sun_path), "%s", endpoint_.c_str());
    if (listener_ < 0 || bind(listener_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 ||
        chmod(endpoint_.c_str(), 0600) != 0 || listen(listener_, 4) != 0)
        throw std::runtime_error(std::string("Cannot open the private password channel: ") + std::strerror(errno));
}
void Authentication::disconnect() {
    if (client_ >= 0) close(client_);
    client_ = -1; ready_ = false; prompt_.clear();
}
void Authentication::poll(bool allowed) {
    if (!allowed) cancel();
    if (listener_ < 0) return;
    if (client_ < 0) {
        client_ = accept4(listener_, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
        if (client_ < 0) return;
        if (!allowed || !sameUser(client_)) { cancel(); return; }
    }
    char request[2048];
    ssize_t count = recv(client_, request, sizeof(request), MSG_DONTWAIT | MSG_TRUNC | (ready_ ? MSG_PEEK : 0));
    if (count < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) disconnect();
        return;
    }
    if (count <= 1 || count > static_cast<ssize_t>(sizeof(request)) || ready_ || request[0] != 'Q') {
        cancel(); return;
    }
    prompt_.assign(request + 1, static_cast<size_t>(count - 1));
    for (char& c : prompt_) if (static_cast<unsigned char>(c) < 32) c = ' ';
    ready_ = true; ++requestId_;
}
bool Authentication::answer(const char* password) {
    if (!ready_ || !password || !password[0]) return false;
    const size_t length = strnlen(password, 1024);
    if (length >= 1024 || std::strpbrk(password, "\r\n")) return false;
    PasswordBuffer packet; packet.bytes[0] = 'P';
    std::memcpy(packet.bytes.data() + 1, password, length);
    const bool sent = sendPacket(client_, packet.bytes.data(), length + 1);
    disconnect();
    return sent;
}
void Authentication::cancel() {
    if (client_ >= 0) sendPacket(client_, "C", 1);
    disconnect();
}
int askpassMain(int argc, char** argv) {
    disableCoreDumps();
    const char* endpoint = std::getenv("XOVIUM_AUTH_SOCKET");
    const char* parent = std::getenv("XOVIUM_AUTH_PID");
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    if (!endpoint || !parent || !*parent || std::strlen(endpoint) >= sizeof(address.sun_path)) {
        std::fprintf(stderr, "Xovium: password panel unavailable. Close and reopen install.sh, then retry.\n"); return 1;
    }
    char* end = nullptr; const long expected = std::strtol(parent, &end, 10);
    if (*end || expected <= 0) return 1;
    std::snprintf(address.sun_path, sizeof(address.sun_path), "%s", endpoint);
    int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    if (fd < 0 || connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 || !sameUser(fd, expected)) {
        if (fd >= 0) close(fd);
        std::fprintf(stderr, "Xovium: cannot reach the installer's password panel. Reopen install.sh and retry.\n"); return 1;
    }
    std::string prompt = "Q";
    prompt += argc > 1 ? argv[1] : "Linux account password";
    if (prompt.size() > 2048) prompt.resize(2048);
    if (!sendPacket(fd, prompt.data(), prompt.size())) { close(fd); return 1; }
    PasswordBuffer response;
    ssize_t count;
    do { count = recv(fd, response.bytes.data(), response.bytes.size(), MSG_TRUNC); } while (count < 0 && errno == EINTR);
    close(fd);
    if (count <= 1 || count > static_cast<ssize_t>(response.bytes.size()) || response.bytes[0] != 'P') {
        std::fprintf(stderr, "Xovium: password request canceled or installer closed. No password was sent to sudo.\n"); return 1;
    }
    const size_t length = static_cast<size_t>(count - 1);
    if (std::memchr(response.bytes.data() + 1, '\0', length) || std::memchr(response.bytes.data() + 1, '\n', length)) return 1;
    size_t offset = 1;
    while (offset < static_cast<size_t>(count)) {
        ssize_t written = write(STDOUT_FILENO, response.bytes.data() + offset, static_cast<size_t>(count) - offset);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return 1;
        offset += static_cast<size_t>(written);
    }
    return write(STDOUT_FILENO, "\n", 1) == 1 ? 0 : 1;
}
