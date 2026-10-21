#include "raylib.h"
#include "rlImGui.h"
#include "imgui.h"
#include "process.hpp"
#include "auth.hpp"
#include "requirements.hpp"
#include "px4_versions.hpp"
#include "imgui_internal.h"
namespace ImStb {
#include "imstb_textedit.h"
}
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unistd.h>
namespace fs = std::filesystem;
namespace ui = ImGui;
static const ImVec4 accent{0.34f, 0.69f, 0.97f, 1};
static const ImVec4 muted{0.57f, 0.63f, 0.72f, 1};
static const ImVec4 green{0.44f, 0.83f, 0.64f, 1};
static const ImVec4 amber{1.f, 0.72f, 0.40f, 1};
static ImFont *bodyFont = nullptr, *titleFont = nullptr;
static const char* sharedWorlds[] = {"Baylands", "Dynamic terrain"};
static const char* sharedWorldIds[] = {"baylands", "dynamic"};
static const char* sharedModels[] = {"Cessna airplane", "R1 rover", "Own model..."};
static const char* sharedModelIds[] = {"rc_cessna", "r1_rover", "custom"};
static const char* modelAdapters[] = {"Aircraft (Cessna adapter)", "Rover (R1 adapter)"};
static const char* simNames[] = {"Gazebo Jetty", "Gazebo Harmonic", "FlightGear", "Isaac Sim (PhysX)", "Lightweight simulation", "Unreal Engine"};
struct SharedSelection {
    int world = 0, model = 0, baseModel = 0;
    std::array<char, 4096> modelPath{};
    int adapter() const { return model == 2 ? baseModel : model; }
};

static const char* qgcVersions[] = {"5.0.8", "5.0.7", "5.0.6"};
static const char* qgcVersionLabels[] = {"5.0.8 (kit-tested default)", "5.0.7", "5.0.6"};
static const char* routerKeys[] = {"px4_source", "qgc", "primary_app", "primary_app_commands",
    "secondary_app_1", "secondary_app_1_commands", "secondary_app_2", "secondary_app_2_commands",
    "secondary_app_3", "secondary_app_3_commands", "tcp"};
static const char* routerRoles[] = {"PX4 input (UDP)", "QGC (UDP)", "Primary app telemetry", "Primary app commands",
    "App 2 telemetry", "App 2 commands", "App 3 telemetry", "App 3 commands", "App 4 telemetry", "App 4 commands", "TCP clients"};
static const char* routerDirections[] = {"PX4 sends here; router listens.", "Router sends here; QGC listens and replies.",
    "Router sends here; application listens.", "Application sends commands here; router listens.",
    "Router sends here; application listens.", "Application sends commands here; router listens.",
    "Router sends here; application listens.", "Application sends commands here; router listens.",
    "Router sends here; application listens.", "Application sends commands here; router listens.",
    "Router listens for TCP clients. Set 0 to disable."};
using RouterPorts = std::array<int, 11>;
static const RouterPorts defaultRouterPorts{14550, 14552, 14540, 14580, 14541, 14581, 14542, 14582, 14543, 14583, 5760};
std::string routerError(const RouterPorts& ports) {
    for (size_t i = 0; i < ports.size(); ++i) {
        int port = ports[i];
        if ((port < 1024 || port > 65535) && !(i == 10 && port == 0)) return "Use ports 1024..65535, or 0 to disable TCP.";
        if (i < 10) {
            for (int base : {18570, 14280, 13030, 19450}) if (port >= base && port <= base + 254) return "A UDP port overlaps a PX4 local socket range.";
            for (size_t j = 0; j < i; ++j) if (port == ports[j]) return "Each UDP role needs a different port.";
        }
    }
    return {};
}

struct Selection {
    bool autopilot = false, sitl = false;
    bool jetty = true, harmonic = false, sih = false, flightgear = false, unreal = false;
    bool isaacPhysx = false, qgc = true, router = false;
    bool airspeed = false, lidar = false, camera = false, license = false;
    int qgcVersion = 0, px4Version = 0;
    RouterPorts routerPorts = defaultRouterPorts;
    int backend = 0, seed = 42, jobs = 4;
    std::array<char, 4096> prefix{}, archive{};
    SharedSelection shared;
    bool selected(int i) const {
        return i == 0 ? jetty : i == 1 ? harmonic : i == 2 ? flightgear :
               i == 3 ? isaacPhysx : i == 4 ? sih : unreal;
    }
    const Px4Release& px4() const { return px4Releases[px4Version]; }
    void selectPx4(int version) {
        px4Version = version;
        if (!px4().jetty && jetty) { jetty = false; harmonic = true; backend = 1; }
        if (!px4().nativeBridges) { flightgear = false; unreal = false; isaacPhysx = false; }
        if (px4().legacyModels) {
            airspeed = false; lidar = false;
        }
    }
    bool gazebo() const { return jetty || harmonic; }
    bool isaac() const { return isaacPhysx; }
    bool any() const { return gazebo() || sih || flightgear || unreal || isaac(); }
    int diskBudgetGiB() const {
        int result = 10;
        for (int i = 0; i < 6; ++i) if (selected(i)) {
            result += simulatorRequirements[i].diskGiB;
        }
        if (any() && shared.world == 1) result += 10;
        if (qgc) result += 2;
        if (router) result += 1;
        return result;
    }
    std::string json() const {
        std::ostringstream out;
        out << "{\n\"schema\":5,\"autopilot\":\"PX4\",\"mode\":\"SITL\",\n";
        auto flag = [&](const char* key, bool value) { out << jsonString(key) << ':' << (value ? "true" : "false") << ",\n"; };
        auto str = [&](const char* key, const std::string& value) { out << jsonString(key) << ':' << jsonString(value) << ",\n"; };
        str("px4_version", px4().version);
        str("qgc_version", qgcVersions[qgcVersion]);
        out << "\"router_ports\":{";
        for (size_t i = 0; i < routerPorts.size(); ++i) { if (i) out << ','; out << jsonString(routerKeys[i]) << ':' << routerPorts[i]; }
        out << "},\n";
        str("prefix", prefix.data()); str("unreal_archive", archive.data());
        str("backend", backend == 0 ? "jetty" : "harmonic"); str("world", sharedWorldIds[shared.world]);
        str("gazebo_model", sharedModelIds[shared.adapter()]); str("flightgear_model", sharedModelIds[shared.adapter()]);
        out << "\"shared_selection\":{";
        str("world", sharedWorldIds[shared.world]); str("model", sharedModelIds[shared.model]);
        str("model_path", shared.model == 2 ? shared.modelPath.data() : "");
        out << "\"model_base\":" << jsonString(sharedModelIds[shared.adapter()]) << "},\n";
        flag("jetty", jetty); flag("harmonic", harmonic); flag("sih", sih); flag("flightgear", flightgear);
        flag("unreal", unreal); flag("isaac_sih", false); flag("isaac_physx", isaacPhysx);
        flag("qgc", qgc); flag("router", router); flag("airspeed", airspeed); flag("lidar", lidar);
        flag("camera", camera); flag("license", license);
        out << "\"seed\":" << seed << ",\"jobs\":" << jobs << "\n}\n";
        return out.str();
    }
};

enum class Job { None, Defaults, Preview, Install, RemovePreview, Remove };
struct App {
    fs::path kit, logPath;
    Selection s;
    Process worker, picker;
    Authentication auth;
    PasswordBuffer password;
    uint64_t shownAuthRequest = 0;
    ImGuiID passwordWidget = 0;
    bool authVisible = false;
    Job job = Job::None;
    int step = 0, furthest = 0, pickerTarget = 0, selectionStep = 0;
    bool simulatorTabsInitialized = false, toolsEdited = false, sharedEdited = false;
    RouterPorts routerDraft = defaultRouterPorts;
    bool previewOk = false, removalReady = false, removeConfirmed = false;
    bool autoScroll = true, closeRequested = false, wrapLogs = true;
    bool prefixEdited = false, previewPending = false, resetLog = true;
    uint64_t logRevision = 0;
    float previousLogY = 0;
    std::string displayedLog;
    std::string error, defaultsPrefix, stage, reviewed, installedPx4;

    std::vector<std::string> command(const std::string& action) const {
        return {"python3", "-u", "-B", (kit / "backend/wizard.py").string(), action};
    }
    void defaults() {
        std::snprintf(s.prefix.data(), s.prefix.size(), "%s", (kit.parent_path() / "xovium").c_str());
        defaultsPrefix = s.prefix.data();
        worker.start(command("--defaults"), {}, true); job = Job::Defaults;
    }
    fs::path request() {
        fs::create_directories(kit / ".build/requests");
        std::string name = (kit / ".build/requests/profile-XXXXXX").string();
        std::vector<char> buffer(name.begin(), name.end()); buffer.push_back(0);
        int fd = mkstemp(buffer.data());
        if (fd < 0) throw std::runtime_error("Cannot save the installation request.");
        close(fd);
        std::ofstream file(buffer.data());
        file << s.json();
        file.close();
        if (!file) throw std::runtime_error("Cannot write the installation request.");
        return fs::path(buffer.data());
    }
    void launch(Job next) {
        if (worker.running() || picker.running()) return;
        try {
            error.clear();
            const bool removing = next == Job::Remove || next == Job::RemovePreview;
            auto args = command(next == Job::Install ? "--install" : next == Job::Preview ? "--preview" : next == Job::Remove ? "--uninstall" : "--uninstall-preview");
            if (!removing) args.push_back(request().string());
            fs::create_directories(kit / ".build/logs");
            auto now = std::chrono::system_clock::now().time_since_epoch().count();
            logPath = kit / ".build/logs" / (std::to_string(now) + ".log");
            if (!worker.start(args, logPath)) { error = worker.error; return; }
            job = next;
            resetLog = true; autoScroll = true;
            if (next == Job::Preview) { previewOk = false; reviewed = s.json(); }
            if (next == Job::RemovePreview) { removalReady = false; removeConfirmed = false; }
            stage = removing ? "Checking managed installation" : "Checking your selection";
            if (next == Job::Install) selectionStep = 2;
            if (next == Job::Install || next == Job::Remove) step = 7;
        } catch (const std::exception& e) { error = e.what(); }
    }
    void poll() {
        bool wasRunning = worker.running();
        worker.poll();
        if (wasRunning && !worker.running()) {
            if (job == Job::Defaults) {
                if (worker.status == 0) {
                    std::istringstream stream(worker.output);
                    std::string path, accepted;
                    std::getline(stream, path); std::getline(stream, accepted);
                    if (!path.empty()) {
                        defaultsPrefix = path;
                        if (!prefixEdited) std::snprintf(s.prefix.data(), s.prefix.size(), "%s", path.c_str());
                    }
                    s.license = accepted == "accepted";
                    std::string version, port;
                    if (std::getline(stream, version)) {
                        if (!toolsEdited) {
                            for (int i = 0; i < IM_ARRAYSIZE(qgcVersions); ++i) if (version == qgcVersions[i]) s.qgcVersion = i;
                        }
                    }
                    RouterPorts parsed = defaultRouterPorts; bool complete = true;
                    for (auto& value : parsed) {
                        if (!std::getline(stream, port)) { complete = false; break; }
                        try { size_t used; value = std::stoi(port, &used); if (used != port.size()) complete = false; }
                        catch (...) { complete = false; }
                    }
                    if (!toolsEdited && complete && routerError(parsed).empty()) s.routerPorts = parsed;
                    std::string px4Version, existing;
                    if (std::getline(stream, px4Version)) {
                        if (!toolsEdited) {
                            for (int i = 0; i < IM_ARRAYSIZE(px4Releases); ++i) if (px4Version == px4Releases[i].version) s.selectPx4(i);
                        }
                        if (std::getline(stream, existing) && existing == "installed") installedPx4 = px4Version;
                    }
                    std::string world, model, modelPath, modelBase;
                    if (!sharedEdited && std::getline(stream, world) && std::getline(stream, model) &&
                        std::getline(stream, modelPath) && std::getline(stream, modelBase)) {
                        for (int i = 0; i < IM_ARRAYSIZE(sharedWorldIds); ++i) if (world == sharedWorldIds[i]) s.shared.world = i;
                        for (int i = 0; i < IM_ARRAYSIZE(sharedModelIds); ++i) if (model == sharedModelIds[i]) s.shared.model = i;
                        for (int i = 0; i < 2; ++i) if (modelBase == sharedModelIds[i]) s.shared.baseModel = i;
                        if (modelPath.size() < s.shared.modelPath.size())
                            std::snprintf(s.shared.modelPath.data(), s.shared.modelPath.size(), "%s", modelPath.c_str());
                    }
                } else error = worker.output + worker.diagnostics;
                worker.output.clear(); job = Job::None;
            } else if (job == Job::Install && worker.status == 0) defaultsPrefix = s.prefix.data();
            else if (job == Job::Preview) previewOk = worker.status == 0 && reviewed == s.json();
            else if (job == Job::RemovePreview) removalReady = worker.status == 0;
        }
        auth.poll(worker.running() && (job == Job::Install || job == Job::Remove));
        picker.poll();
        if (pickerTarget && !picker.running()) {
            if (picker.status == 0) {
                try {
                    std::string value = pickerPath(picker.output);
                    auto& target = pathTarget(pickerTarget);
                    if (value.size() >= target.size()) throw std::runtime_error("The selected path is too long.");
                    std::snprintf(target.data(), target.size(), "%s", value.c_str());
                    if (pickerTarget == 2) prefixEdited = true;
                    if (pickerTarget == 3) sharedEdited = true;
                    error.clear(); previewOk = false;
                    previewPending = step == 6;
                } catch (const std::exception& e) { error = e.what(); }
            } else if (picker.status != 1 || !picker.diagnostics.empty() || !picker.output.empty()) {
                error = "File chooser failed: " + picker.diagnostics + picker.output +
                        " You can type or paste the full path in the field.";
            }
            pickerTarget = 0;
        }
        if (previewPending && !worker.running()) {
            previewPending = false;
            if (step == 6) launch(Job::Preview);
        }
        if (worker.running() && (job == Job::Install || job == Job::Remove)) {
            auto pos = worker.output.rfind("[install] ");
            if (pos != std::string::npos) stage = worker.output.substr(pos, worker.output.find('\n', pos) - pos);
        }
    }
    std::array<char, 4096>& pathTarget(int target) {
        if (target == 1) return s.archive;
        if (target == 2) return s.prefix;
        if (target == 3) return s.shared.modelPath;
        throw std::runtime_error("Unknown file chooser target.");
    }
    void choosePath(int target) {
        if (picker.running()) return;
        const bool directory = target == 2;
        std::string title = target == 1 ? "Select the official UE 5.6.1 Linux ZIP" : target == 2 ?
            "Choose the installation folder" : "Select your shared model.sdf";
        std::vector<std::string> args = {"zenity", "--file-selection", "--title=" + title};
        if (directory) args.push_back("--directory");
        else {
            const char* filter = target == 1 ? "ZIP | *.zip" : "Shared SDF model | model.sdf";
            args.push_back(std::string("--file-filter=") + filter);
        }
        const char* current = pathTarget(target).data();
        if (*current) {
            std::string initial = current;
            if (directory && initial.back() != '/') initial += '/';
            args.push_back("--filename=" + initial);
        }
        if (picker.start(args, {}, true)) pickerTarget = target;
        else error = "File chooser unavailable. Type or paste the full path in the field.";
    }
    void navigate(int target) {
        if (target > 0 && !s.sitl) target = 0;
        if (target > 1 && !s.autopilot) target = 1;
        step = target;
        if (!s.gazebo() && step == 5) step = 6;
        furthest = std::max(furthest, step);
        if (step == 6) launch(Job::Preview);
    }
    void manage() {
        if (step < 7) selectionStep = step;
        step = 8;
    }
    void returnToSelection() {
        previewOk = false;
        navigate(std::clamp(selectionStep, 0, std::min(furthest, 6)));
    }
    void back() {
        int previous = step - 1;
        if (!s.gazebo() && previous == 5) previous = 4;
        step = std::max(0, previous); previewOk = false;
    }
};

void hint(const char* text) { ui::PushStyleColor(ImGuiCol_Text, muted); ui::TextWrapped("%s", text); ui::PopStyleColor(); }
void heading(const char* title, const char* description) {
    ui::PushFont(titleFont); ui::TextWrapped("%s", title); ui::PopFont();
    ui::Spacing(); hint(description); ui::Dummy({0, 16});
}
void section(const char* label) {
    ui::Dummy({0, 9}); ui::TextColored(accent, "%s", label); ui::Separator(); ui::Dummy({0, 5});
}
void check(const char* title, bool& value, const char* description) {
    ui::Checkbox(title, &value);
    ui::Indent(29); hint(description); ui::Unindent(29); ui::Dummy({0, 4});
}
bool primary(const char* label, ImVec2 size = {180, 42}) {
    ui::PushStyleColor(ImGuiCol_Button, ImVec4{0.16f, 0.43f, 0.66f, 1});
    ui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4{0.22f, 0.53f, 0.78f, 1});
    bool result = ui::Button(label, size); ui::PopStyleColor(2); return result;
}
void line(const char* key, const std::string& value) {
    ui::TextColored(muted, "%s", key);
    ui::SameLine(std::max(172.f, ui::GetItemRectMax().x - ui::GetWindowPos().x + 12.f));
    ui::TextWrapped("%s", value.c_str());
}
void logs(App& a, float height) {
    bool toEnd = ui::Checkbox("Follow output", &a.autoScroll) && a.autoScroll;
    ui::SameLine(); if (ui::SmallButton("Latest")) { a.autoScroll = true; toEnd = true; }
    ui::SameLine(); const bool reflow = ui::Checkbox("Wrap lines", &a.wrapLogs);
    ui::SameLine(); if (ui::SmallButton("Copy")) ui::SetClipboardText(a.displayedLog.c_str());
    ui::SameLine(); if (ui::SmallButton("Full log") && !a.logPath.empty()) openUrl(a.logPath.string());
    const auto flags = ImGuiWindowFlags_AlwaysVerticalScrollbar |
                       (a.wrapLogs ? ImGuiWindowFlags_None : ImGuiWindowFlags_HorizontalScrollbar);
    ui::BeginChild("output", {0, height}, ImGuiChildFlags_Borders, flags);
    const float y = ui::GetScrollY(), maximum = ui::GetScrollMaxY();
    const bool hovered = ui::IsWindowHovered(ImGuiHoveredFlags_AllowWhenBlockedByActiveItem);
    const bool upwardWheel = hovered && ui::GetIO().MouseWheel > 0;
    const bool scrollDrag = ui::IsMouseDown(ImGuiMouseButton_Left) && y < maximum - 2 && (hovered || y < a.previousLogY - 2);
    const bool keyboardScroll = ui::IsWindowFocused() &&
        (ui::IsKeyPressed(ImGuiKey_PageUp) || ui::IsKeyPressed(ImGuiKey_Home) || ui::IsKeyPressed(ImGuiKey_UpArrow));
    if (!a.resetLog && !toEnd && !reflow && (upwardWheel || scrollDrag || keyboardScroll))
        a.autoScroll = false;
    bool updated = false;
    if (a.resetLog || (a.autoScroll && (a.logRevision != a.worker.outputRevision || toEnd))) {
        a.displayedLog = a.worker.output;
        a.logRevision = a.worker.outputRevision;
        updated = true;
    }
    ui::PushStyleColor(ImGuiCol_Text, ImVec4{0.72f, 0.79f, 0.84f, 1});
    if (a.wrapLogs) ui::PushTextWrapPos(0);
    ui::TextUnformatted(a.displayedLog.c_str());
    if (a.wrapLogs) ui::PopTextWrapPos();
    ui::PopStyleColor();
    if (a.autoScroll && (updated || toEnd || reflow)) ui::SetScrollHereY(1.f);
    a.previousLogY = y;
    a.resetLog = false;
    ui::EndChild();
}

void requirements(int simulator) {
    const auto& spec = simulatorRequirements[simulator];
    ui::PushID(simulator); ui::Indent(29);
    hint(("Free space: about " + std::to_string(spec.diskGiB) + " GiB extra (kit estimate; shared PX4 counted once).").c_str());
    hint(spec.cpuRam); hint(spec.gpu);
    if (ui::SmallButton("Requirements source ↗")) openUrl(spec.source);
    ui::Unindent(29); ui::Dummy({0, 6}); ui::PopID();
}
void routerAdvanced(App& a) {
    ui::SetNextWindowSize({780, 620}, ImGuiCond_Appearing);
    if (ui::BeginPopupModal("MAVLink Router - Advanced", nullptr, ImGuiWindowFlags_NoResize)) {
        hint("Choose the port for each role. PX4 and QGC are configured together; other applications must use the matching telemetry/command ports.");
        if (ui::BeginTable("Ports", 3, ImGuiTableFlags_BordersInnerH | ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY, {0, 345})) {
            ui::TableSetupColumn("Role", ImGuiTableColumnFlags_WidthFixed, 205);
            ui::TableSetupColumn("Port", ImGuiTableColumnFlags_WidthFixed, 85);
            ui::TableSetupColumn("Direction / purpose", ImGuiTableColumnFlags_WidthStretch);
            ui::TableSetupScrollFreeze(0, 1); ui::TableHeadersRow();
            for (int i = 0; i < 11; ++i) {
                ui::PushID(i); ui::TableNextRow(); ui::TableNextColumn(); ui::TextWrapped("%s", routerRoles[i]);
                ui::TableNextColumn(); ui::SetNextItemWidth(-1); ui::InputInt("##port", &a.routerDraft[i], 0, 0);
                ui::TableNextColumn(); ui::TextWrapped("%s", routerDirections[i]); ui::PopID();
            }
            ui::EndTable();
        }
        hint("UDP is used unless marked TCP. Existing listen/destination addresses are preserved. Restart the managed router after installation to apply changes.");
        const auto error = routerError(a.routerDraft);
        if (!error.empty()) ui::TextColored(amber, "%s", error.c_str());
        if (ui::Button("Reset defaults")) a.routerDraft = defaultRouterPorts;
        ui::SameLine(); ui::BeginDisabled(!error.empty());
        if (ui::Button("Apply")) { a.s.routerPorts = a.routerDraft; a.toolsEdited = true; a.previewOk = false; ui::CloseCurrentPopup(); }
        ui::EndDisabled(); ui::SameLine(); if (ui::Button("Cancel")) ui::CloseCurrentPopup();
        ui::EndPopup();
    }
}
void sharedTools(App& a) {
    auto& s = a.s;
    hint("These tools are shared by all selected simulators.");
    if (ui::Checkbox("QGroundControl", &s.qgc)) a.toolsEdited = true;
    if (s.qgc) {
        ui::SetNextItemWidth(235);
        if (ui::Combo("QGC version", &s.qgcVersion, qgcVersionLabels, IM_ARRAYSIZE(qgcVersionLabels))) a.toolsEdited = true;
        hint("Official Linux AppImages with verified checksums. 5.0.8 is the kit-tested default.");
    }
    if (ui::Checkbox("MAVLink Router", &s.router)) a.toolsEdited = true;
    ui::SameLine(); ui::BeginDisabled(!s.router);
    if (ui::Button("Advanced...")) { a.routerDraft = s.routerPorts; ui::OpenPopup("MAVLink Router - Advanced"); }
    ui::EndDisabled();
    hint(("Automatic QGC link: UDP " + std::to_string(s.routerPorts[1]) + ". The router must be running to relay telemetry.").c_str());
    routerAdvanced(a);
}
void simulators(App& a) {
    auto& s = a.s;
    heading("Choose your simulators", "Selections stay active across tabs. Configure QGroundControl and MAVLink Router in Shared tools.");
    hint("Space figures include builds and caches. Reserve 10 GiB once for shared PX4/tools; custom models need extra space.");
    if (ui::BeginTabBar("Simulator types")) {
        const char* tabs[] = {"Recommended", "Lightweight", "FlightGear", "Isaac Sim", "Shared tools"};
        for (int tab = 0; tab < IM_ARRAYSIZE(tabs); ++tab) {
            auto flags = !a.simulatorTabsInitialized && tab == 0 ? ImGuiTabItemFlags_SetSelected : ImGuiTabItemFlags_None;
            if (!ui::BeginTabItem(tabs[tab], nullptr, flags)) continue;
            ui::Dummy({0, 10});
            if (tab == 0) {
                section("GAZEBO");
                ui::BeginDisabled(!s.px4().jetty); ui::Checkbox("Gazebo Jetty", &s.jetty); ui::EndDisabled();
                hint("PX4 support: 1.17.0 native; 1.16.0–1.16.2 with a compatibility adapter. Unavailable for 1.15.x.");
                if (s.jetty && s.px4().jetty == 1) {
                    ui::PushStyleColor(ImGuiCol_Text, amber);
                    ui::TextWrapped("(PX4 source will be modified to support this simulator)"); ui::PopStyleColor();
                } else if (!s.px4().jetty) hint("Choose Harmonic below, or select PX4 1.16.x / 1.17.0 on the Autopilot page.");
                ui::Checkbox("Gazebo Harmonic", &s.harmonic);
                hint("PX4 support: 1.15.0–1.15.4, 1.16.0–1.16.2 and 1.17.0 (native).");
                if (s.jetty && s.harmonic) {
                    ui::SetNextItemWidth(200); ui::Combo("Active PX4 backend", &s.backend, "Jetty\0Harmonic\0");
                    hint("Both can be installed. One Gazebo ABI is active in PX4; switching later requires a rebuild.");
                } else if (s.gazebo()) s.backend = s.jetty ? 0 : 1;
                requirements(s.backend);
                section("UNREAL ENGINE");
                ui::BeginDisabled(!s.px4().nativeBridges); ui::Checkbox("Unreal Engine 5", &s.unreal); ui::EndDisabled();
                hint("PX4 support: 1.15.0–1.15.4, 1.16.0–1.16.2 and 1.17.0."); requirements(5);
                if (s.unreal) {
                    if (ui::Button("UE 5.6.1  ↗  Epic download / sign in")) openUrl("https://www.unrealengine.com/en-US/linux");
                    hint("Download the official Linux ZIP from Epic, then select it here.");
                    ui::SetNextItemWidth(std::max(180.f, ui::GetContentRegionAvail().x - 108));
                    ui::InputTextWithHint("##archive", "/path/to/Linux_Unreal_Engine_5.6.1.zip", s.archive.data(), s.archive.size());
                    ui::SameLine(); if (ui::Button("Browse...##ue")) a.choosePath(1);
                }
            } else if (tab == 1) {
                check("Lightweight simulation", s.sih, "Runs PX4 built-in airplane physics or the native R1 rover ground model without a 3D simulator. QGroundControl is optional."); requirements(4);
            } else if (tab == 2) {
                ui::BeginDisabled(!s.px4().nativeBridges); ui::Checkbox("FlightGear 2020.3.19", &s.flightgear); ui::EndDisabled();
                hint("PX4 support: 1.15.0–1.15.4, 1.16.0–1.16.2 and 1.17.0.");
                hint("Ubuntu 24.04 repository version; distribution packaging updates may change the revision."); requirements(2);
            } else if (tab == 3) {
                ui::BeginDisabled(!s.px4().nativeBridges); check("Isaac Sim (PhysX)", s.isaacPhysx, "NVIDIA Isaac Sim supplies physics and 3D visualization. Requires an NVIDIA RTX GPU."); ui::EndDisabled();
                hint("PX4 support: 1.15.0–1.15.4, 1.16.0–1.16.2 and 1.17.0."); requirements(3);
                if (s.isaac()) {
                    ui::Checkbox("I accept the NVIDIA Omniverse license", &s.license);
                    ui::SameLine(); if (ui::SmallButton("Read ↗")) openUrl("https://docs.omniverse.nvidia.com/platform/latest/common/NVIDIA_Omniverse_License_Agreement.html");
                }
            } else sharedTools(a);
            ui::EndTabItem();
        }
        ui::EndTabBar(); a.simulatorTabsInitialized = true;
    }
    std::string selected;
    for (int i = 0; i < 6; ++i) if (s.selected(i)) { if (!selected.empty()) selected += ", "; selected += simNames[i]; }
    hint(("Selected: " + (selected.empty() ? std::string("none") : selected)).c_str());
}

void worldsPage(App& a) {
    heading("Choose a world", "One shared world is used by every selected simulator.");
    section("SHARED WORLD");
    ui::SetNextItemWidth(-1);
    if (ui::Combo("##shared-world", &a.s.shared.world, sharedWorlds, IM_ARRAYSIZE(sharedWorlds))) {
        a.sharedEdited = true; a.previewOk = false;
    }
    if (a.s.shared.world == 0)
        hint("Baylands is installed in shared/worlds/baylands and converted for each selected simulator.");
    else {
        hint("Streams geographic terrain using shared/worlds/dynamic_terrain/terrain.json. All simulators reuse the shared terrain cache.");
        hint("Allow another 10 GiB for builds and the initial terrain cache (estimate); downloaded terrain can grow.");
    }
    if (a.s.sih)
        hint("Lightweight simulation: the rover collides with the selected terrain. PX4 built-in airplane physics retains its flat ground; the selected world is shown in the optional viewer.");
}
void modelsPage(App& a) {
    heading("Choose a model", "One shared model is used by every selected simulator.");
    auto& shared = a.s.shared;
    section("SHARED MODEL");
    ui::SetNextItemWidth(-1);
    if (ui::Combo("##shared-model", &shared.model, sharedModels, IM_ARRAYSIZE(sharedModels))) {
        a.sharedEdited = true; a.previewOk = false;
    }
    if (shared.model == 2) {
        ui::TextUnformatted("Your model.sdf");
        ui::SetNextItemWidth(std::max(180.f, ui::GetContentRegionAvail().x - 108));
        if (ui::InputTextWithHint("##shared-model-path", "/path/to/your/model.sdf", shared.modelPath.data(), shared.modelPath.size())) {
            a.sharedEdited = true; a.previewOk = false;
        }
        ui::SameLine(); if (ui::Button("Browse...##shared-model")) a.choosePath(3);
        ui::Dummy({0, 8}); ui::TextUnformatted("Physics and PX4 control mapping");
        ui::SetNextItemWidth(-1);
        if (ui::Combo("##shared-model-adapter", &shared.baseModel, modelAdapters, IM_ARRAYSIZE(modelAdapters))) {
            a.sharedEdited = true; a.previewOk = false;
        }
        hint("The model and its referenced assets are copied into shared/models and converted for every selected simulator. Keep meshes and textures with model.sdf.");
        hint("Choose the matching aircraft or rover adapter. Native simulators use that adapter's physics and controls; custom SDF dynamics are not translated automatically.");
    }
    if (a.s.flightgear && shared.adapter() == 0)
        hint("FlightGear uses the shared airplane visual with Rascal YASim flight dynamics; this is an approximation.");
    if (a.s.sih)
        hint("Lightweight simulation uses PX4's built-in airplane physics or native differential-drive rover physics.");
    section("YOUR SHARED LIBRARY");
    hint("Edit common sources in shared/models and shared/worlds. The conversion layer is in shared/transform.");
}
void sensorsPage(App& a) {
    heading("Configure Gazebo sensors", "Optional sensors are added to a generated model. The original PX4 models remain available.");
    section("STANDARD FLIGHT SENSORS");
    bool required = true;
    ui::BeginDisabled();
    ui::Checkbox("IMU", &required); ui::SameLine(155); ui::Checkbox("GPS", &required);
    ui::SameLine(285); ui::Checkbox("Barometer", &required); ui::SameLine(465); ui::Checkbox("Magnetometer", &required);
    ui::EndDisabled(); hint("The selected airframe keeps its standard PX4 sensor setup.");
    section("OPTIONAL PAYLOADS");
    ui::BeginDisabled(a.s.px4().legacyModels);
    check("Airspeed", a.s.airspeed, "Add Gazebo's airspeed sensor and connect it to PX4. Fixed-wing defaults otherwise use PX4's simulated airspeed.");
    check("Downward lidar", a.s.lidar, "Add the supplied LW20 range sensor with PX4 distance telemetry.");
    ui::EndDisabled();
    if (a.s.px4().legacyModels) hint("Extra airspeed/lidar payload assets require PX4 1.16.x or 1.17.0. Standard airframe sensors remain available.");
    check("RGB camera", a.s.camera, "Add the supplied camera; images are published on Gazebo Transport.");
    ui::Dummy({0, 10}); hint("Payload selections can be combined. They configure simulation assets; validate the resulting vehicle before flight experiments.");
}
void reviewPage(App& a) {
    auto& s = a.s;
    heading("Review & install", "Check the destination and plan. Start downloading will install your selection and create the runtime scripts.");
    section("INSTALL LOCATION");
    ui::SetNextItemWidth(std::max(180.f, ui::GetContentRegionAvail().x - 108));
    if (ui::InputText("##prefix", s.prefix.data(), s.prefix.size())) { a.previewOk = false; a.prefixEdited = true; }
    ui::SameLine(); if (ui::Button("Browse...##prefix")) a.choosePath(2);
    hint("Select or create an empty installation folder. Software goes directly into this folder. Moving an existing installation uses --cli --relocate.");
    ui::SetNextItemWidth(220); if (ui::SliderInt("Build jobs", &s.jobs, 1, 32)) a.previewOk = false;
    line("Space estimate", std::to_string(s.diskBudgetGiB()) + " GiB for a fresh installation; custom assets need extra space");
    section("SELECTION");
    line("Execution mode", "SITL");
    line("Autopilot", std::string("PX4 ") + s.px4().version);
    if (s.jetty && s.px4().jetty == 1) line("Jetty compatibility", "PX4 source will be modified to support this simulator.");
    std::string chosen;
    auto add = [&](bool yes, const char* text) { if (yes) { if (!chosen.empty()) chosen += ", "; chosen += text; } };
    add(s.jetty,"Jetty"); add(s.harmonic,"Harmonic"); add(s.sih,"Lightweight simulation"); add(s.flightgear,"FlightGear");
    add(s.unreal,"UE 5.6.1"); add(s.isaacPhysx,"Isaac Sim (PhysX)");
    line("Simulators", chosen);
    if (s.gazebo()) {
        line("Gazebo backend", s.backend == 0 ? "Jetty" : "Harmonic");

        std::string sensors = "Standard sensors";
        if (s.airspeed) sensors += ", airspeed";
        if (s.lidar) sensors += ", lidar";
        if (s.camera) sensors += ", RGB camera";
        line("Sensors", sensors);
    }
    line("Shared world", sharedWorlds[s.shared.world]);
    line("Shared model", sharedModels[s.shared.model]);
    if (s.shared.model == 2) {
        line("Model source", s.shared.modelPath.data());
        line("Physics adapter", modelAdapters[s.shared.baseModel]);
    }
    line("Tools", (s.qgc ? std::string("QGroundControl ") + qgcVersions[s.qgcVersion] : std::string("None")) + (s.router ? " / MAVLink Router" : ""));
    if (s.router) {
        line("Router input / QGC", std::to_string(s.routerPorts[0]) + " / " + std::to_string(s.routerPorts[1]) + " UDP");
        line("App telemetry / commands", std::to_string(s.routerPorts[2]) + " / " + std::to_string(s.routerPorts[3]) + " UDP (primary app)");
    }
    ui::Dummy({0, 10});
    if (a.worker.running()) ui::TextColored(accent, "Checking the installation plan...");
    else if (a.previewOk) ui::TextColored(green, "Plan validated. Ready to download.");
    else ui::TextColored(amber, "Validate the plan before starting.");
    ui::BeginDisabled(a.worker.running() || a.picker.running()); if (ui::Button("Validate plan")) a.launch(Job::Preview); ui::EndDisabled();
    if (!a.worker.output.empty()) {
        if (ui::CollapsingHeader("Installation plan / validation details", a.previewOk ? ImGuiTreeNodeFlags_None : ImGuiTreeNodeFlags_DefaultOpen)) logs(a, 170);
    }
    hint("Existing managed components are retained. System package changes request your Linux password here in the installer. Large simulator downloads may take a while.");
}
void runPage(App& a) {
    const bool removing = a.job == Job::Remove;
    const bool running = a.worker.running();
    const bool okay = !running && a.worker.status == 0;
    heading(running ? (removing ? "Removing the installation" : "Installing your workspace") : okay ? (removing ? "Software removed" : "Your workspace is ready") : a.worker.stopping ? "Installation stopped" : "Operation needs attention",
            running ? "Live output is shown below. Completed components are recorded so you can resume after an interruption." : okay ? "The installer kit stays here for future changes and maintenance." : "Read the last error below. You can return to the selection and retry.");
    if (running) {
        ui::TextColored(accent, "%s", a.worker.stopping ? "Stopping the installer; waiting for its processes..." : a.auth.pending() ? "Waiting for your system password" : a.stage.c_str());
        ui::ProgressBar(-static_cast<float>(GetTime()), {-1, 6}, "");
    } else if (okay && !removing) {
        ui::TextColored(green, "Run a simulator from:");
        ui::TextWrapped("%s/scripts/run.sh", a.s.prefix.data());
        if (ui::Button("Open installed folder")) openUrl(a.s.prefix.data());
    }
    ui::Dummy({0, 8}); logs(a, std::max(160.f, ui::GetContentRegionAvail().y - 85));
    hint(("Full log: " + a.logPath.string()).c_str());
}
void clearPassword(App& a) {
    a.password.clear();
    if (!ui::GetCurrentContext()) return;
    auto& context = *ui::GetCurrentContext();
    auto& state = context.InputTextState;
    if (a.passwordWidget && state.ID == a.passwordWidget) {
        if (context.ActiveId == a.passwordWidget) ui::ClearActiveID();
        for (auto* buffer : {&state.TextA, &state.TextToRevertTo, &state.CallbackTextBackup}) {
            wipeSecret(buffer->Data, static_cast<size_t>(buffer->Capacity));
            buffer->clear();
        }
        wipeSecret(&state.Stb->undostate, sizeof(state.Stb->undostate));
        state.ID = 0; state.TextLen = 0;
    }
    auto& deactivated = context.InputTextDeactivatedState;
    if (a.passwordWidget && deactivated.ID == a.passwordWidget) {
        wipeSecret(deactivated.TextA.Data, static_cast<size_t>(deactivated.TextA.Capacity));
        deactivated.ClearFreeMemory();
    }
}
void authenticationPanel(App& a) {
    const bool incoming = a.auth.pending() && a.shownAuthRequest != a.auth.requestId();
    if (incoming) {
        clearPassword(a); a.shownAuthRequest = a.auth.requestId(); a.authVisible = true;
        ui::OpenPopup("System package permission");
    }
    ui::SetNextWindowPos(ui::GetMainViewport()->GetCenter(), ImGuiCond_Appearing, {.5f, .5f});
    ui::SetNextWindowSize({560, 0}, ImGuiCond_Always);
    if (ui::BeginPopupModal("System package permission", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        if (!a.auth.pending()) {
            clearPassword(a); a.authVisible = false; ui::CloseCurrentPopup();
        } else {
            ui::TextWrapped("Enter your Linux account password to allow system package changes.");
            ui::TextWrapped("%s", a.auth.prompt().c_str());
            ui::Dummy({0, 8});
            if (ui::IsWindowAppearing()) ui::SetKeyboardFocusHere();
            ui::SetNextItemWidth(-1);
            const bool enter = ui::InputText("##system-password", a.password.bytes.data(), a.password.bytes.size(),
                ImGuiInputTextFlags_Password | ImGuiInputTextFlags_EnterReturnsTrue | ImGuiInputTextFlags_NoUndoRedo);
            a.passwordWidget = ui::GetItemID();
            if (enter && !a.password.bytes[0]) ui::SetKeyboardFocusHere(-1);
            hint("Your password goes to sudo. It is never saved in settings or logs. This panel stays open until you respond.");
            ui::Dummy({0, 8});
            const bool hasPassword = a.password.bytes[0] != 0;
            ui::BeginDisabled(!hasPassword);
            const bool confirm = primary("Authorize", {170, 40}); ui::EndDisabled();
            ui::SameLine(); const bool cancel = ui::Button("Cancel", {130, 40}) || ui::IsKeyPressed(ImGuiKey_Escape);
            if ((enter || confirm) && hasPassword) {
                a.auth.answer(a.password.bytes.data()); clearPassword(a); a.authVisible = false; ui::CloseCurrentPopup();
            } else if (cancel) {
                a.auth.cancel(); clearPassword(a); a.authVisible = false; ui::CloseCurrentPopup();
            }
        }
        ui::EndPopup();
    }
    if (a.authVisible && !a.auth.pending()) { clearPassword(a); a.authVisible = false; }
}

void managePage(App& a) {
    heading("Manage installation", "Remove software installed by this kit while keeping the installer itself.");
    line("Managed location", a.defaultsPrefix);
    hint("Removal deletes the managed simulation folders, runtime data and owned package dependencies. Review the removal plan first. Existing unrelated system packages are retained.");
    ui::Dummy({0, 18});
    ui::BeginDisabled(a.worker.running() || a.picker.running()); if (ui::Button("Uninstall", {200, 40})) a.launch(Job::RemovePreview); ui::EndDisabled();
    if (a.job == Job::RemovePreview) {
        if (!a.worker.output.empty()) logs(a, 230);
        ui::BeginDisabled(!a.removalReady || a.worker.running());
        ui::Checkbox("Remove the managed software and its runtime data", &a.removeConfirmed);
        ui::EndDisabled();
        ui::BeginDisabled(!a.removalReady || !a.removeConfirmed || a.worker.running());
        if (ui::Button("Uninstall software", {200, 42})) a.launch(Job::Remove);
        ui::EndDisabled();
    }
}

void theme() {
    ui::StyleColorsDark(); auto& s = ui::GetStyle();
    s.WindowPadding = {24, 24}; s.FramePadding = {12, 9}; s.ItemSpacing = {12, 10};
    s.WindowRounding = 0; s.ChildRounding = 8; s.FrameRounding = 5; s.PopupRounding = 6;
    s.GrabRounding = 4; s.ScrollbarRounding = 5; s.WindowBorderSize = 0;
    s.Colors[ImGuiCol_WindowBg] = {0.065f, 0.080f, 0.105f, 1};
    s.Colors[ImGuiCol_ChildBg] = {0.078f, 0.095f, 0.125f, 1};
    s.Colors[ImGuiCol_Text] = {0.91f, 0.93f, 0.97f, 1};
    s.Colors[ImGuiCol_TextDisabled] = muted;
    s.Colors[ImGuiCol_FrameBg] = {0.12f, 0.15f, 0.20f, 1};
    s.Colors[ImGuiCol_Button] = {0.14f, 0.18f, 0.24f, 1};
    s.Colors[ImGuiCol_ButtonHovered] = {0.20f, 0.28f, 0.38f, 1};
    s.Colors[ImGuiCol_Header] = {0.12f, 0.23f, 0.34f, 1};
    s.Colors[ImGuiCol_HeaderHovered] = {0.17f, 0.29f, 0.41f, 1};
    s.Colors[ImGuiCol_CheckMark] = accent;
    s.Colors[ImGuiCol_Separator] = {0.18f, 0.23f, 0.29f, 1};
    s.Colors[ImGuiCol_Border] = {0.18f, 0.23f, 0.29f, 1};
}

void draw(App& a) {
    const float width = static_cast<float>(GetScreenWidth()), height = static_cast<float>(GetScreenHeight());
    ui::SetNextWindowPos({0, 0}); ui::SetNextWindowSize({width, height});
    ui::Begin("Xovium", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings);
    ui::BeginChild("sidebar", {214, 0}, ImGuiChildFlags_None);
    ui::TextColored(accent, "X O V I U M"); hint("INSTALLER  /  0.3.0"); ui::Dummy({0, 30});
    const char* steps[] = {"Execution mode", "Autopilot", "Simulators", "Worlds", "Models", "Sensors", "Review & install"};
    for (int i = 0; i < 7; ++i) {
        const bool skipped = !a.s.gazebo() && i == 5;
        const std::string label = std::to_string(i + 1) + "   " + steps[i];
        ui::BeginDisabled(a.worker.running() || a.picker.running() || i > a.furthest || skipped || a.step == 7);
        if (ui::Selectable(label.c_str(), a.step == i, 0, {0, 34})) a.navigate(i);
        ui::EndDisabled();
    }
    ui::SetCursorPosY(std::max(ui::GetCursorPosY() + 24, height - 265));
    ui::Separator(); hint("One shared autopilot.\nYour choice of simulation."); ui::Dummy({0, 10});
    ui::BeginDisabled(a.worker.running() || a.picker.running()); if (ui::Button("Manage installation", {195, 38})) a.manage(); ui::EndDisabled();
    ui::Dummy({0, 12}); hint("raylib 5.5\nImGui 1.92.5 docking\nrlImGui Raylib_5_5");
    ui::EndChild(); ui::SameLine();
    ui::BeginGroup();
    ui::BeginChild("page", {0, height - 124}, ImGuiChildFlags_Borders);
    switch (a.step) {
    case 0:
        heading("Choose an execution mode", "Choose where the autopilot runs before selecting its software."); section("EXECUTION MODE");
        if (ui::Selectable("SITL     /     Software in the loop", a.s.sitl, 0, {0, 62})) a.s.sitl = true;
        hint("Run the autopilot on this computer. Select its software version on the next page.");
        ui::Dummy({0, 24}); ui::BeginDisabled(); ui::Selectable("HITL     /     Coming later", false, 0, {0, 62}); ui::EndDisabled();
        hint("Hardware in the loop uses firmware on the flight controller. This installer currently supports SITL only."); break;
    case 1:
        heading("Which autopilot will you use?", "Select the autopilot for your SITL workspace.");
        section("AUTOPILOT");
        if (ui::Selectable("PX4", a.s.autopilot, 0, {0, 62})) a.s.autopilot = true;
        hint("The same pinned PX4 source is shared by all selected simulators.");
        ui::Dummy({0, 20}); ui::BeginDisabled(); ui::Selectable("ArduPilot     /     Coming later", false, 0, {0, 62}); ui::EndDisabled();
        if (a.s.sitl && a.s.autopilot) {
            section("SITL SOFTWARE VERSION");
            ui::SetNextItemWidth(340);
            if (ui::BeginCombo("PX4 version", a.s.px4().version)) {
                for (int i = 0; i < IM_ARRAYSIZE(px4Releases); ++i) {
                    std::string label = px4Releases[i].version;
                    if (i == 0) label += " (kit default)";
                    if (ui::Selectable(label.c_str(), a.s.px4Version == i)) { a.s.selectPx4(i); a.previewOk = false; }
                }
                ui::EndCombo();
            }
            hint("Stable releases with Harmonic or Jetty support. Unsupported simulator/payload choices are cleared when changing PX4.");
            if (!a.installedPx4.empty() && a.installedPx4 != a.s.px4().version)
                hint(("This workspace uses PX4 " + a.installedPx4 + ". Uninstall the managed workspace before installing a different PX4 version.").c_str());
        }
        break;
    case 2: simulators(a); break;
    case 3: worldsPage(a); break;
    case 4: modelsPage(a); break;
    case 5: sensorsPage(a); break;
    case 6: reviewPage(a); break;
    case 7: runPage(a); break;
    case 8: managePage(a); break;
    }
    if (!a.error.empty()) { ui::Dummy({0, 14}); ui::PushStyleColor(ImGuiCol_Text, amber); ui::TextWrapped("%s", a.error.c_str()); ui::PopStyleColor(); }
    ui::EndChild(); ui::Dummy({0, 9});
    bool busy = a.worker.running() || a.picker.running();
    ui::BeginDisabled(busy || a.step == 0);
    if (ui::Button(a.step >= 7 ? "← Selection" : "← Back", {145, 42})) {
        if (a.step >= 7) a.returnToSelection(); else a.back();
    }
    ui::EndDisabled();
    const float available = ui::GetContentRegionAvail().x;
    ui::SameLine(std::max(170.f, available - 205));
    if (a.step < 6) {
        bool enabled = !busy && (a.step != 0 || a.s.sitl) && (a.step != 1 || a.s.autopilot);
        if (a.step == 2) enabled = enabled && a.s.any() && (!a.s.isaac() || a.s.license) && (!a.s.unreal || a.s.archive[0]);
        if (a.step == 4) enabled = enabled && (a.s.shared.model != 2 || a.s.shared.modelPath[0]);
        ui::BeginDisabled(!enabled); if (primary(a.step == 5 ? "Review selection →" : "Continue →", {205, 42})) a.navigate(a.step + 1); ui::EndDisabled();
    } else if (a.step == 6) {
        ui::BeginDisabled(busy || !a.previewOk || a.reviewed != a.s.json());
        if (primary("Start downloading", {205, 42})) a.launch(Job::Install);
        ui::EndDisabled();
    } else if (a.step == 7 && busy) {
        ui::BeginDisabled(a.worker.stopping);
        if (ui::Button("Stop installation", {205, 42})) ui::OpenPopup("Stop this operation?");
        ui::EndDisabled();
    }
    if (ui::BeginPopupModal("Stop this operation?", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ui::TextUnformatted("The current operation will be interrupted.\nCompleted components remain recorded for resume or uninstall.");
        if (ui::Button("Keep running")) ui::CloseCurrentPopup();
        ui::SameLine();
        if (ui::Button("Stop operation")) { a.worker.cancel(); ui::CloseCurrentPopup(); }
        ui::EndPopup();
    }
    if (a.closeRequested) { ui::OpenPopup("Installation is running"); a.closeRequested = false; }
    if (ui::BeginPopupModal("Installation is running", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ui::TextUnformatted("Keep this window open until the operation finishes,\nor request a stop and wait for cleanup.");
        if (ui::Button("Keep open")) ui::CloseCurrentPopup();
        ui::SameLine();
        if (ui::Button("Stop and wait")) { a.worker.cancel(); ui::CloseCurrentPopup(); }
        ui::EndPopup();
    }
    authenticationPanel(a);
    ui::EndGroup(); ui::End();
}

int main(int argc, char** argv) {
    App app;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--kit" && i + 1 < argc) app.kit = fs::absolute(argv[++i]);
        else { std::fprintf(stderr, "Unknown GUI option: %s\n", arg.c_str()); return 2; }
    }
    if (app.kit.empty() || !fs::is_regular_file(app.kit / "backend/wizard.py")) { std::fprintf(stderr, "Run the kit's install.sh.\n"); return 2; }
    if (!std::getenv("DISPLAY") && !std::getenv("WAYLAND_DISPLAY")) { std::fprintf(stderr, "A desktop display is required for the wizard. Use install.sh --cli for terminal installation.\n"); return 1; }
    setenv("XOVIUM_KIT_ROOT", app.kit.c_str(), 1);
    setenv("SUDO_ASKPASS", (app.kit / "app/askpass.sh").c_str(), 1);
    SetTraceLogLevel(LOG_WARNING);
    SetConfigFlags(FLAG_WINDOW_RESIZABLE | FLAG_VSYNC_HINT | FLAG_WINDOW_ALWAYS_RUN);
    InitWindow(1160, 850, "Xovium Installer 0.3.0");
    if (!IsWindowReady()) return 1;
    try {
        app.auth.start();
        setenv("XOVIUM_AUTH_SOCKET", app.auth.endpoint().c_str(), 1);
        setenv("XOVIUM_AUTH_PID", std::to_string(getpid()).c_str(), 1);
    } catch (const std::exception& e) { std::fprintf(stderr, "%s\n", e.what()); CloseWindow(); return 1; }
    SetWindowMinSize(980, 700); SetExitKey(KEY_NULL); SetTargetFPS(60);
    rlImGuiBeginInitImGui();
    auto& io = ui::GetIO(); io.IniFilename = nullptr; io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    const char* font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf";
    if (fs::is_regular_file(font)) {
        bodyFont = io.Fonts->AddFontFromFileTTF(font, 17);
        titleFont = io.Fonts->AddFontFromFileTTF(font, 29);
    } else { ImFontConfig cfg; cfg.SizePixels = 18; bodyFont = io.Fonts->AddFontDefault(&cfg); titleFont = bodyFont; }
    io.FontDefault = bodyFont;
    theme(); rlImGuiEndInitImGui();
    app.defaults();
    while (true) {
        if (WindowShouldClose()) { if (app.worker.running() && (app.job == Job::Install || app.job == Job::Remove)) app.closeRequested = true; else break; }
        app.poll();
        BeginDrawing(); ClearBackground({16, 20, 27, 255}); rlImGuiBegin(); draw(app); rlImGuiEnd(); EndDrawing();
    }
    app.auth.cancel(); clearPassword(app);
    rlImGuiShutdown(); CloseWindow(); return 0;
}
