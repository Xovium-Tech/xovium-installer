PX4_VERSION = "1.16.0"
PX4_COMMIT = "6ea3539157ca358c70a515878b77077af7d4611d"
QGC_VERSION = "5.0.8"
QGC_SHA256 = "06969c67ef58ea063def0a8271447a1cc385438c4a7df36813315b4475146737"
QGC_RELEASES = {
    "5.0.8": QGC_SHA256,
    "5.0.7": "3f1ce90fc4f50e8675ac53c156112bfec45eaf7ba057b10c8a8945635d84967f",
    "5.0.6": "64a6c50580cd8d56907e4a1d92261e5f95e049dfb0ffe23cb31c2ea53b57240e",
}
ISAAC_VERSION = "6.1.0.0"
PUBLIC = ("px4", "qgc", "flightgear", "unreal-engine", "isaac", "sih", "sih-core",
          "gazebo-jetty", "gazebo-harmonic", "gazebo-plugins", "mavlink-router")
BUILD = "build-essential ccache cmake file git libssl-dev libxml2-dev libxml2-utils ninja-build pkg-config python3-dev python3-venv rsync unzip zip".split()
GST = "libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav".split()
GAZEBO_BUILD = ["libopencv-dev", "libeigen3-dev"] + GST
PACKAGES = {
    "px4": BUILD + ["bc"],
    "qgc": "libfuse2t64 libxcb-cursor0 libxkbcommon-x11-0 libxcb-xinerama0 libxcb-render-util0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-shape0 libxcb-xkb1 libopengl0 libpulse0".split() + GST,
    "flightgear": ["flightgear", "xdotool", "libeigen3-dev"] + BUILD,
    "isaac-runtime": "python3.12 python3.12-venv libegl1 libgl1 libvulkan1 libxt6t64 libxrandr2 libxi6 libxrender1 libxkbcommon0 libnss3 libasound2t64".split(),
    "isaac": BUILD + GST + "7zip curl libcurl4-openssl-dev libpng-dev python3.12-dev".split(),
    "sih": [], "sih-core": [],
    "unreal-engine": BUILD + "bc curl git-lfs xz-utils libvulkan1 vulkan-tools mesa-vulkan-drivers libgl1 libegl1 libsdl2-2.0-0 libnss3 libnspr4 libgbm1 libasound2t64 libpulse0 libx11-6 libxrandr2 libxinerama1 libxcursor1 libxi6 libxss1 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxkbcommon-x11-0 libgtk-3-0t64 libfreetype6 libfontconfig1 libatk1.0-0t64 libatk-bridge2.0-0t64 libdrm2 libglu1-mesa".split(),
    "gazebo-harmonic": ["gz-harmonic", "libgz-transport13-dev", "libgz-sim8-dev"] + GAZEBO_BUILD,
    "gazebo-jetty": ["gz-jetty", "libgz-transport15-dev", "libgz-sim10-dev"] + GAZEBO_BUILD,
    "gazebo-plugins": BUILD + GST + "libcurl4-openssl-dev libopencv-dev libprotobuf-dev protobuf-compiler".split(),
    "mavlink-router": "git meson ninja-build pkg-config gcc g++ libsystemd-dev".split(),
}


def dependencies(name, backend):
    return {
        "flightgear": ["px4"], "isaac": ["px4", "isaac-runtime"],
        "sih": ["sih-core", "isaac-runtime"], "sih-core": ["px4"], "unreal-engine": ["px4"],
        "gazebo-plugins": [f"gazebo-{backend}", "px4"],
        "gazebo-harmonic": ["px4"], "gazebo-jetty": ["px4"],
    }.get(name, [])


def resolve(selected, backend):
    result = []
    def visit(name):
        for dep in dependencies(name, backend):
            visit(dep)
        if name not in result:
            result.append(name)
    for name in selected:
        visit(name)
    return result


def packages_for(plan, backend):
    packages = {"ca-certificates", "curl"}
    for name in plan:
        packages.update(PACKAGES[name])
    if "gazebo-plugins" in plan:
        for distro in ('harmonic', 'jetty'):
            if f'gazebo-{distro}' in plan or distro == backend:
                sim, plugin = (8, 2) if distro == 'harmonic' else (10, 4)
                packages.update([f"libgz-sim{sim}-dev", f"libgz-plugin{plugin}-dev",
                                 f"libgz-rendering{sim}-ogre2-dev"])
    return sorted(packages)
