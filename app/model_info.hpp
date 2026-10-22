#pragma once
#include <array>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

struct ModelInfo {
    std::string id, name, path, status;
    bool available = false, editable = false;
    std::vector<std::string> sensors, plugins;
};

inline std::array<ModelInfo, 2> emptyModelInfo() {
    std::array<ModelInfo, 2> models{};
    models[0].id = "cessna"; models[0].name = "Cessna airplane";
    models[1].id = "rover"; models[1].name = "R1 rover";
    return models;
}

// Fixed, newline-framed backend response; values cannot contain line breaks.
inline std::array<ModelInfo, 2> parseModelInfo(const std::string& response) {
    std::istringstream input(response);
    auto read = [&]() {
        std::string value;
        if (!std::getline(input, value) || value.find('\0') != std::string::npos)
            throw std::runtime_error("Incomplete model information from the backend.");
        if (!value.empty() && value.back() == '\r') value.pop_back();
        return value;
    };
    if (read() != "XOVIUM_MODEL_INFO_V1")
        throw std::runtime_error("The backend returned an unsupported model information format.");
    auto flag = [&]() {
        const auto value = read();
        if (value != "0" && value != "1") throw std::runtime_error("Invalid model availability.");
        return value == "1";
    };
    auto attachments = [&]() {
        const auto value = read();
        if (value.empty() || value.size() > 4 || value.find_first_not_of("0123456789") != std::string::npos)
            throw std::runtime_error("Invalid attachment count in model information.");
        const auto count = std::stoul(value);
        if (count > 4096) throw std::runtime_error("Too many attachments in model information.");
        std::vector<std::string> result;
        for (size_t i = 0; i < count; ++i) result.push_back(read());
        return result;
    };
    auto models = emptyModelInfo();
    std::array<bool, 2> seen{};
    for (int i = 0; i < 2; ++i) {
        const auto id = read();
        const int index = id == "cessna" ? 0 : id == "rover" ? 1 : -1;
        if (index < 0 || seen[index]) throw std::runtime_error("Invalid model identifiers from the backend.");
        seen[index] = true;
        auto& model = models[index];
        model.name = read(); model.path = read();
        model.available = flag(); model.editable = flag(); model.status = read();
        model.sensors = attachments(); model.plugins = attachments();
        if (model.name.empty()) model.name = index == 0 ? "Cessna airplane" : "R1 rover";
    }
    std::string remaining;
    while (std::getline(input, remaining))
        if (!remaining.empty() && remaining != "\r") throw std::runtime_error("Unexpected data after model information.");
    return models;
}
