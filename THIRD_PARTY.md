# Third-party code

The root [LICENSE](LICENSE) covers Xovium's original code. Bundled third-party
files retain their own notices:

- `backend/assets/flightgear/vehicle_state.cpp`: copyright 2020 ThunderFly s.r.o.,
  BSD 3-Clause. The full notice is retained in the file.
- `backend/assets/unreal/native/vendor/terrain`: Xovium Tech dynamic terrain
  core, BSD 3-Clause; its full license is retained in that directory.

The shared library seeds RC Cessna and R1 rover assets from the selected PX4
Gazebo model checkout, retaining their included notices. The Baylands scene
downloads OpenRobotics' **baylands** and **Coast Water**, version 3, from Gazebo
Fuel under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution,
source URLs and archive hashes are installed in
`shared/worlds/baylands/sources.json`. Xovium converts their meshes, materials,
coordinates and collision data into native simulator formats. Dynamic terrain
imagery and elevation remain subject to their providers' terms; provider URLs
are editable in the shared terrain configuration.

FlightGear's Cessna adapter copies the Rascal YASim dynamics by Curtis Olson
from the PX4 FlightGear bridge. Its source comments and GPLv3 license are kept
beside the copied dynamics in the shared template and generated aircraft folder.

The GUI bootstrap downloads raylib, Dear ImGui and rlImGui at the revisions in
`app/dependencies.json`. Their licenses are kept with the downloaded sources in
`.build/deps/`. These dependencies are not bundled in the source release.

PX4, QGroundControl, Gazebo, FlightGear, MAVLink Router, NVIDIA Isaac Sim and
Unreal Engine are installed separately under their respective licenses. The
installer's license does not replace those terms.
