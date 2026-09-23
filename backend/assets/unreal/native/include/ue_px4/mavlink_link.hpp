#pragma once
#include "flight_model.hpp"
#include <cstdint>
#include <memory>
#include <string>

namespace ue_px4 {
class MavlinkLink {
public:
    enum class ReceiveResult { Controls,Timeout,Disconnected,Error };
    MavlinkLink();
    ~MavlinkLink();
    MavlinkLink(const MavlinkLink&)=delete;
    MavlinkLink& operator=(const MavlinkLink&)=delete;
    bool listen(int port,std::string& error);
    bool accept_client(double timeout_seconds,std::string& error);
    bool send_state(const State&,std::uint64_t time_usec,std::string& error);
    ReceiveResult receive_controls(Controls&,double timeout_seconds,std::string& error);
    void close();
    int port() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
