# Xovium Installer

Install PX4 and a choice of flight simulators on Ubuntu 24.04 x86-64. The desktop wizard sets up the software, models, worlds, QGroundControl and MAVLink Router in one workspace.

This is a source-only release. The installer builds its interface on the first run and downloads the components you select.

## 1. Get the installer

```bash
git clone https://github.com/Xovium-Tech/xovium-installer.git
cd xovium-installer
```

You can also download the ZIP from [Releases](https://github.com/Xovium-Tech/xovium-installer/releases), extract it and open a terminal in the extracted folder.

You need Ubuntu 24.04 x86-64, Python 3.12, an internet connection and a user account with sudo access. The installer lists and installs any missing build packages when it starts. Run it as your normal user.

## 2. Choose what to install

```bash
./install.sh
```

1. Select PX4 and its version. The default is **1.16.0**.
2. Choose your simulators, plus QGroundControl and MAVLink Router if needed.
3. Select a world, model and optional sensors for each simulator.
4. Review the installation folder and disk requirements, then click **Start downloading**.

| Simulator | Notes |
| --- | --- |
| PX4 SIH | Runs PX4's built-in airplane simulation without a visual simulator. |
| Gazebo Harmonic | Supports all PX4 versions offered by the installer. |
| Gazebo Jetty | Supports PX4 1.16.x through an adapter and 1.17.0 natively. |
| FlightGear | Installs the Ubuntu package and the PX4 bridge. |
| Unreal Engine 5.6.1 | Download the Linux ZIP from Epic and select it in the wizard. |
| Isaac PhysX | Requires an NVIDIA RTX GPU and acceptance of NVIDIA's license. |

Only PX4 SITL is currently available. The wizard shows hardware and disk requirements for each simulator.

The default installation folder is `xovium`, beside the installer folder. You can change it in the wizard. Rerunning the installer reuses completed components; unchecking a component does not uninstall it.

## 3. Launch a simulation

From the installer folder:

```bash
XOVIUM_PREFIX=$(./install.sh --cli --print-prefix)
"$XOVIUM_PREFIX/scripts/run.sh"
```

Choose a simulator from the menu, or launch one directly:

```bash
"$XOVIUM_PREFIX/scripts/run.sh" gazebo-harmonic --qgc
"$XOVIUM_PREFIX/scripts/run.sh" sih --no-qgc
```

Choose custom models and worlds in the wizard. Telemetry ports can be changed under MAVLink Router → Advanced.

## Manage or remove an installation

```bash
./install.sh --cli --status
./install.sh --uninstall --dry-run
./install.sh --uninstall
```

Uninstall removes managed software and system packages added for it. It keeps the installer, its build prerequisites and pre-existing packages. Use `--keep-system-packages` to remove only the workspace files.

For more commands, run `./install.sh --cli --help`. If setup was interrupted, rerun the installer to resume.

## Build from source

To build the interface without opening it:

```bash
./install.sh --build-only
```

## License

BSD 3-Clause. Copyright (c) 2026 Alex Chazov. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY.md).
