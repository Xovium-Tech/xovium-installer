# Third-party code

The root [LICENSE](LICENSE) covers Xovium's original code. Bundled third-party
files retain their own notices:

- `backend/assets/flightgear/vehicle_state.cpp`: copyright 2020 ThunderFly s.r.o.,
  BSD 3-Clause. The full notice is retained in the file.

The GUI bootstrap downloads raylib, Dear ImGui and rlImGui at the revisions in
`app/dependencies.json`. Their licenses are kept with the downloaded sources in
`.build/deps/`. These dependencies are not bundled in the source release.

PX4, QGroundControl, Gazebo, FlightGear, MAVLink Router, NVIDIA Isaac Sim and
Unreal Engine are installed separately under their respective licenses. The
installer's license does not replace those terms.
