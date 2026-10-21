# Xovium Installer

[![CI](https://github.com/Xovium-Tech/xovium-installer/actions/workflows/ci.yml/badge.svg)](https://github.com/Xovium-Tech/xovium-installer/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/Xovium-Tech/xovium-installer)](https://github.com/Xovium-Tech/xovium-installer/releases/latest)

Set up PX4 SITL, simulators, shared models and worlds, QGroundControl and MAVLink Router in one workspace on Ubuntu 24.04 x86-64. The desktop wizard downloads and builds the components you select. Releases contain source code; the installer builds its interface on first run.

## Supported versions

| Component | Supported target |
| --- | --- |
| Operating system | Ubuntu 24.04, x86-64 |
| PX4 SITL | **1.15.0–1.15.4**, **1.16.0–1.16.2**, **1.17.0**; default **1.16.0** |
| Gazebo Harmonic | All PX4 versions listed above |
| Gazebo Jetty | PX4 1.16.x with the installer compatibility adapter; 1.17.0 natively |
| Lightweight simulation | PX4's built-in airplane physics (SIH), or Xovium's native rover ground physics; optional Isaac viewer |
| FlightGear | Ubuntu 24.04 package with the PX4 bridge |
| Unreal Engine | 5.6.1, from a user-supplied Epic Linux ZIP |
| Isaac Sim | NVIDIA Isaac Sim with Xovium's native PhysX/PX4 bridge; see [known limitations](#known-limitations) |
| QGroundControl | 5.0.6, 5.0.7, **5.0.8**; default **5.0.8** |

Only PX4 SITL is available. PX4 1.12–1.14 are not offered by this installer. Additional Gazebo airspeed/lidar payload assets require PX4 1.16.x or 1.17.0; standard airframe sensors remain available on 1.15.x.

## 1. Dependencies

Use Ubuntu 24.04 x86-64 with Python 3.12, an internet connection and a user account with sudo access. Run the installer as your normal user.

The GUI needs a C++17 compiler, CMake 3.22 or newer, Git, pkg-config, X11/OpenGL development packages, Zenity and xdg-utils. The bootstrap lists and installs missing packages. It downloads pinned raylib, Dear ImGui and rlImGui sources from [app/dependencies.json](app/dependencies.json).

Simulator dependencies are installed for the selected components. Isaac Sim requires an NVIDIA RTX GPU and acceptance of NVIDIA's license. Unreal Engine requires the Linux ZIP downloaded from Epic. The wizard shows each simulator's hardware and disk requirements before installation.

## 2. Get the source and build

```bash
git clone https://github.com/Xovium-Tech/xovium-installer.git
cd xovium-installer
./install.sh --build-only
```

You can also download a source archive from [Releases](https://github.com/Xovium-Tech/xovium-installer/releases), extract it and run the same build command in that folder. The interface is built at `.build/cmake/xovium-installer`.

## 3. Use

Open the wizard:

```bash
./install.sh
```

1. Select PX4 and its version. The default is **1.16.0**.
2. Choose your simulators. The **Recommended** tab contains Gazebo (selected by default) and Unreal Engine. Add QGroundControl and MAVLink Router in **Shared tools** if needed.
3. Select one shared world and model for every simulator, plus optional Gazebo sensors.
4. Review the installation folder and disk requirements, then click **Start downloading**.

The default installation folder is `xovium`, beside the installer folder. You can change it in the wizard. Rerunning the installer reuses completed components; unchecking a component does not uninstall it.

From the installer folder, launch the simulator menu:

```bash
XOVIUM_PREFIX=$(./install.sh --cli --print-prefix)
"$XOVIUM_PREFIX/scripts/run.sh"
```

Or launch one directly:

```bash
"$XOVIUM_PREFIX/scripts/run.sh" gazebo-harmonic --qgc
"$XOVIUM_PREFIX/scripts/run.sh" sih --no-qgc
```

The wizard offers **Baylands** or **dynamic_terrain**, and **Cessna**, **rover**, or **your own model**. Each choice applies to every installed simulator, including simulators retained from an earlier installation. PX4's simulator-specific bundled assets remain available in its source tree. Telemetry ports can be changed under MAVLink Router → Advanced.

### Manage or remove an installation

```bash
./install.sh --cli --status
./install.sh --uninstall --dry-run
./install.sh --uninstall
```

Uninstall removes managed software and system packages added for it, including the shared library. Back up edited models and worlds first. It keeps the installer, its build prerequisites and pre-existing packages. Use `--keep-system-packages` to remove only the workspace files.

For more commands, run `./install.sh --cli --help`. If setup was interrupted, rerun the installer to resume.

## Configuration

### Shared models and worlds

Installing a simulator creates a common asset library inside the installation:

```text
xovium/
  shared/
    models/
      cessna/source/                 # PX4 RC Cessna SDF, meshes and textures
      rover/source/                  # PX4 R1 differential rover
      rover/physics.json             # Isaac, FlightGear and lightweight rover dynamics
      custom/source/                 # Optional imported model.sdf, meshes and textures
    worlds/
      baylands/source/               # World plus local Baylands and water meshes
      dynamic_terrain/terrain.json    # Shared origin, imagery/elevation and cache settings
      dynamic_terrain/source/        # Streamed Gazebo world source
    transform/                       # Converters, native templates and Unreal importer
    cache/                           # Verified downloads and terrain cache
  simulation/<simulator>/
    models/                          # Generated simulator assets
    worlds/
    bridge/
    plugins/
```

The installer converts the common sources to Gazebo SDF, Isaac/SIH USD, FlightGear AC3D/XML, and Unreal FBX plus imported native packages. Baylands is downloaded once from pinned Gazebo Fuel releases with SHA-256 verification; its exported worlds reference local assets. Dynamic terrain uses the providers and origin in `shared/worlds/dynamic_terrain/terrain.json` and downloads tiles as needed.

Isaac streams surrounding terrain while physics continues over loaded ground. It waits at startup, or when collision terrain near the vehicle is missing; distant tiles do not pause the simulation. Nearby tiles take priority, and scene changes are spread across frames.

For your own model, select `model.sdf` in a self-contained folder and choose the Cessna or rover physics adapter. The installer imports the folder into `shared/models/custom/source`; edit that installed copy afterward. Resources must be local to the folder, with direct SDF links and visible geometry (expand nested models/includes first). Native exports reuse the selected adapter's physics and collision layout. Gazebo keeps your SDF dynamics, so its joints, sensors and plugins must match the selected PX4 airframe.

Edit the files under `shared`, then regenerate installed simulator exports:

```bash
./install.sh --refresh-assets
```

Shared source edits survive installer reruns. Regeneration refuses to overwrite locally edited simulator exports. `scripts/models.sh shared --open` opens the source models. The converter can also write a separate, empty export directory for inspection:

```bash
python3 "$XOVIUM_PREFIX/shared/transform/convert.py" \
  --simulator flightgear --output /path/to/empty-export
```

The R1 rover uses differential steering and reversible PX4 motor controls in every simulator. Gazebo supplies its native vehicle dynamics; Isaac uses PhysX vehicle dynamics; Unreal uses Chaos suspension and tire forces; FlightGear and the lightweight option use Xovium's native ground FDM. FlightGear renders the same streamed terrain geometry used by the rover's ground sampler. `rover/physics.json` configures the Isaac, FlightGear and lightweight adapters; Gazebo reads its SDF dynamics, and Unreal uses its compiled Chaos rover parameters.

To add shared assets to an older installation, rerun its selected installer sections so the new dependencies and bridges are built before using `--refresh-assets`.

## Testing

[CI](https://github.com/Xovium-Tech/xovium-installer/actions/workflows/ci.yml) builds the desktop installer on Ubuntu 24.04 and checks Python and shell sources. Simulator integration requires the corresponding installed runtimes and is not part of CI. A successful installer build does not establish compatibility with every GPU, driver or simulator combination.

## Known limitations

- An extended Isaac session has been observed to terminate with **SIGSEGV (exit 139)** inside NVIDIA's `libomni.graph.core.plugin.so`. The trigger is undetermined; camera switching or missing terrain has not been established as the cause. This observed crash remains unresolved.
- Dynamic terrain is streamed around the vehicle, rather than an independently moved editor camera. Views beyond the loaded region can show its boundary.
- Geometry conversion does not translate simulator-specific aerodynamic plugins. Cessna retains each bridge's airplane dynamics; FlightGear uses the supplied Rascal YASim dynamics as an approximation.
- PX4's built-in airplane SIH does not gain terrain collision from a viewer world. Rover ground physics does use the chosen Baylands heightfield or streamed elevation.

## License

BSD 3-Clause. Copyright (c) 2026 Xovium Tech. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY.md). Downloaded simulators, assets and dependencies retain their own licenses.
