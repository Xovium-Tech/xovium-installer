#pragma once
struct SimulatorRequirements {
    int diskGiB;
    const char* cpuRam;
    const char* gpu;
    const char* source;
};
inline constexpr SimulatorRequirements simulatorRequirements[] = {
    {8, "Kit recommendation: 4-core Intel/AMD x86-64; 8 GB RAM (16 GB preferred).",
     "NVIDIA or AMD GPU; OpenGL >3.3, preferably 4.3+. Allow 4 GB VRAM for cameras/terrain (kit recommendation).",
     "https://gazebosim.org/docs/jetty/troubleshooting/"},
    {8, "Kit recommendation: 4-core Intel/AMD x86-64; 8 GB RAM (16 GB preferred).",
     "NVIDIA or AMD GPU; OpenGL >3.3, preferably 4.3+. Allow 4 GB VRAM for cameras/terrain (kit recommendation).",
     "https://gazebosim.org/docs/harmonic/troubleshooting/"},
    {15, "4-core Intel/AMD around 2 GHz; plan for 8 GB RAM, preferably 16 GB with scenery.",
     "NVIDIA or AMD GPU with working OpenGL/GLSL drivers. A dedicated GPU is recommended; scenery grows on disk.",
     "https://wiki.flightgear.org/Hardware_Recommendations"},
    {100, "Intel Core i7 (7th gen) / AMD Ryzen 5 or better; 4+ cores; 32 GB RAM minimum, 64 GB preferred.",
     "NVIDIA RTX 4080 / 16 GB VRAM or supported equivalent. AMD GPUs and NVIDIA GPUs without RT cores are unsupported.",
     "https://docs.isaacsim.omniverse.nvidia.com/6.1.0/installation/requirements.html"},
    {1, "Kit recommendation: 2+ Intel/AMD x86-64 CPU cores; 4 GB RAM to run, 8 GB for builds.",
     "No dedicated GPU needed for PX4 SIH physics. QGC still needs a graphical desktop.",
     "https://docs.px4.io/v1.16/en/sim_sih/"},
    {150, "Quad-core Intel/AMD 2.5 GHz or faster; 32 GB RAM recommended for the editor/builds.",
     "NVIDIA or AMD GPU with Vulkan and 8+ GB VRAM. More demanding rendering features need newer GPUs.",
     "https://dev.epicgames.com/documentation/en-us/unreal-engine/linux-development-requirements-for-unreal-engine"},
};
