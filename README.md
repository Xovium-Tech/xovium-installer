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

Only PX4 SITL is available. PX4 1.12–1.14 are not offered by this installer. Model sensors and plugins are edited in Xovium Model Editor; their simulator and PX4 support depends on the selected runtime.

## 1. Dependencies

Use Ubuntu 24.04 x86-64 with Python 3.12, an internet connection and a user account with sudo access. Run the installer as your normal user.

The GUI and model editor need a C++17 compiler, CMake 3.22 or newer, Git, pkg-config, X11/OpenGL development packages, Assimp, libpng, tinyxml2, libarchive, Zenity and xdg-utils. The bootstrap lists and installs missing packages. GUI dependencies are downloaded at pinned revisions.

Simulator dependencies are installed for the selected components. Isaac Sim requires an NVIDIA RTX GPU and acceptance of NVIDIA's license. Unreal Engine requires the Linux ZIP downloaded from Epic. The wizard shows each simulator's hardware and disk requirements before installation.

## 2. Get the source and build

```bash
git clone --recurse-submodules https://github.com/Xovium-Tech/xovium-installer.git
cd xovium-installer
./install.sh --build-only
```

The [model editor](https://github.com/Xovium-Tech/xovium-model-editor) is a pinned Git submodule at `tools/xovium-model-editor`; GitHub displays that folder as a repository link. For an existing clone, run `git submodule update --init --recursive`. A plain clone is also supported: starting the installer initializes the missing editor submodule.

You can also download a source archive from [Releases](https://github.com/Xovium-Tech/xovium-installer/releases), extract it and run the same build command in that folder. GitHub archives omit submodule contents, so the bootstrap fetches the exact editor revision recorded in `backend/editor-source.json`. The interface is built at `.build/cmake/xovium-installer`.

## 3. Use

Open the wizard:

```bash
./install.sh
```

1. Select PX4 and its version. The default is **1.16.0**.
2. Choose your simulators. The **Recommended** tab contains Gazebo (selected by default) and Unreal Engine. Add QGroundControl and MAVLink Router in **Shared tools** if needed.
3. Select one shared world and model for every simulator.
4. On **Sensors**, inspect the Cessna and rover cards. Each lists the sensors and plugins attached to that model. **Change model** opens it in the editor; save and close the editor to refresh the card.
5. Review the installation folder and disk requirements, then click **Start downloading**.

The default installation folder is `xovium`, beside the installer folder. You can change it in the wizard. Before installation, edited models are staged in the installer kit for that installation folder; choose the destination before editing models. Untouched models continue to come from the selected PX4 release. After installation, **Change model** opens the installed `.xovmodel` directly. Rerunning the installer reuses completed components; unchecking a component does not uninstall it.

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
  tools/xovium-model-editor/          # Installed desktop editor and native tools
  shared/
    models/
      cessna/model.xovmodel          # Editable portable Cessna project
      rover/model.xovmodel           # Editable portable rover project
      cessna/source/                 # Original PX4 SDF, meshes and textures
      rover/source/                  # Original PX4 R1 source
      rover/physics.json             # Isaac, FlightGear and lightweight rover dynamics
      custom/model.xovmodel          # Optional vehicle selected in the installer
    worlds/
      baylands/source/               # World plus local Baylands and water meshes
      dynamic_terrain/terrain.json    # Shared origin, imagery/elevation and cache settings
      dynamic_terrain/source/        # Streamed Gazebo world source
    transform/model-tool/            # Native C++ .xovmodel converter
    transform/cache/model-exports/   # Conversion signatures and output hashes
    transform/                       # Runtime adapters, world converters and Unreal importer
    cache/                           # Verified downloads and terrain cache
  simulation/<simulator>/
    models/                          # Generated simulator assets
    worlds/
    bridge/
    plugins/
```

Before each simulator starts, Xovium checks the `.xovmodel` bundles under `shared/models`. It uses the C++ model converter to prepare Gazebo SDF, Isaac/lightweight USD, FlightGear AC3D/XML, or Unreal native packages. Unchanged bundles reuse their existing outputs. A changed bundle, converter or adapter, or a missing output triggers conversion for the requested simulator. Concurrent starts share a conversion lock; a failed conversion stops startup and preserves the previous complete model. Locally edited generated files are protected from replacement.

Baylands is downloaded once from pinned Gazebo Fuel releases with SHA-256 verification; its exported worlds reference local assets. Dynamic terrain uses the providers and origin in `shared/worlds/dynamic_terrain/terrain.json` and downloads tiles as needed.

Isaac streams surrounding terrain while physics continues over loaded ground. It waits at startup, or when collision terrain near the vehicle is missing; distant tiles do not pause the simulation. Nearby tiles take priority, and scene changes are spread across frames.

Open `shared/models/cessna/model.xovmodel` or `shared/models/rover/model.xovmodel` in [Xovium Model Editor](https://github.com/Xovium-Tech/xovium-model-editor), edit it, and save to the same path. Launch Xovium normally; conversion happens before simulator/PX4 processes start. Extra bundles can live in their own folders under `shared/models`; they are converted into the simulator's model library too. To select a different vehicle for launch, choose it as the installer's custom model.

The installed editor runs independently of the installer checkout:

```bash
"$XOVIUM_PREFIX/tools/xovium-model-editor/run.sh" \
  "$XOVIUM_PREFIX/shared/models/cessna/model.xovmodel"
```

For your own model, select an `.xovmodel` file and the Cessna or rover physics adapter in the installer. It becomes `shared/models/custom/model.xovmodel`; edit this installed copy afterward. Self-contained `model.sdf` imports remain supported and are packaged as portable bundles. Native simulators retain the selected adapter's physics and collision layout, while Isaac cameras retain their edited mounting transforms. Other sensor metadata requires the corresponding runtime support. Gazebo keeps the supplied SDF dynamics and native sensor definitions; joints, plugins and topic routing must match the selected PX4 airframe.

Model changes are checked automatically on launch. After editing shared worlds, regenerate their installed exports:

```bash
./install.sh --refresh-assets
```

Shared source edits survive installer reruns. `scripts/models.sh shared --open` opens the model library. To prepare one simulator's portable models without starting it:

```bash
python3 -B "$XOVIUM_PREFIX/shared/transform/model_cache.py" --simulator flightgear
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
