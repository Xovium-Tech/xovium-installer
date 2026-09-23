#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

void wipeSecret(void* data, size_t size);
struct PasswordBuffer {
    std::array<char, 1024> bytes{};
    ~PasswordBuffer() { clear(); }
    void clear() { wipeSecret(bytes.data(), bytes.size()); }
};

class Authentication {
public:
    Authentication() = default;
    Authentication(const Authentication&) = delete;
    Authentication& operator=(const Authentication&) = delete;
    ~Authentication();
    void start();
    void poll(bool allowed);
    bool pending() const { return ready_; }
    uint64_t requestId() const { return requestId_; }
    const std::string& prompt() const { return prompt_; }
    const std::string& endpoint() const { return endpoint_; }
    bool answer(const char* password);
    void cancel();
private:
    int listener_ = -1, client_ = -1;
    bool ready_ = false;
    uint64_t requestId_ = 0;
    std::string directory_, endpoint_, prompt_;
    void disconnect();
};

int askpassMain(int argc, char** argv);
