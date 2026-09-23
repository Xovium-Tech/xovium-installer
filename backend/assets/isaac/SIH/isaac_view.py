import argparse
import math
import signal
import sys
import threading
import time
from pathlib import Path

from mavlink_pose import PoseTracker


PROJECT_DIR = Path(__file__).resolve().parent


class MavlinkReceiver:
    def __init__(self, endpoint, system_id, origin):
        from pymavlink import mavutil

        self.connection = mavutil.mavlink_connection(endpoint, dialect="common")
        self.system_id = system_id
        self.tracker = PoseTracker(origin)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.error = None
        self.count = 0
        self.thread = threading.Thread(target=self._receive, daemon=True)
        self.thread.start()

    def _receive(self):
        try:
            while not self.stop_event.is_set():
                message = self.connection.recv_match(blocking=True, timeout=0.2)
                if message is None or message.get_srcSystem() != self.system_id:
                    continue
                with self.lock:
                    if self.tracker.update(message):
                        self.count += 1
        except Exception as error:
            self.error = error
            self.stop_event.set()

    def snapshot(self):
        if self.error is not None:
            raise RuntimeError(f"MAVLink receive failed: {self.error}") from self.error
        with self.lock:
            return self.tracker.latest, self.count

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1.0)
        self.connection.close()


def check_link(receiver, duration):
    deadline = time.monotonic() + duration
    first_received = None
    while time.monotonic() < deadline:
        pose, count = receiver.snapshot()
        if pose is not None:
            if first_received is None:
                first_received = pose.received_at
            if count >= 20 and pose.received_at - first_received >= 1.0:
                print(f"MAVLink OK: {count} ground-truth poses; ENU={pose.position}; quaternion={pose.orientation}")
                return
        time.sleep(0.05)
    raise RuntimeError("No sustained HIL_STATE_QUATERNION stream. Start run_px4.sh and check the instance/UDP port.")


def prepare_scene(stage, aircraft_usd=None):
    from pxr import Gf, Sdf, Usd, UsdGeom

    if UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.z or not math.isclose(UsdGeom.GetStageMetersPerUnit(stage), 1.0):
        raise ValueError("World USD must use metres and Z up; X is east and Y is north")
    if stage.GetCompositionErrors():
        raise ValueError(f"World USD has unresolved assets: {stage.GetCompositionErrors()}")
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        aircraft = UsdGeom.Xform.Define(stage, "/World/Aircraft")
        aircraft.ClearXformOpOrder()
        translation = aircraft.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
        orientation = aircraft.AddOrientOp(UsdGeom.XformOp.PrecisionDouble)
        if aircraft_usd is None and not stage.GetPrimAtPath("/World/Aircraft/Visual/Asset"):
            aircraft_usd = PROJECT_DIR / "models" / "airplane.usda"
        if aircraft_usd is not None:
            asset_path = Path(aircraft_usd).expanduser().resolve()
            asset_stage = Usd.Stage.Open(str(asset_path))
            if asset_stage is None or not asset_stage.GetDefaultPrim():
                raise ValueError("Aircraft USD must exist and have a default prim")
            if UsdGeom.GetStageUpAxis(asset_stage) != UsdGeom.Tokens.z:
                raise ValueError("Aircraft USD must use Z up, X forward, Y left")
            if asset_stage.GetCompositionErrors():
                raise ValueError(f"Aircraft USD has unresolved assets: {asset_stage.GetCompositionErrors()}")
            visual = UsdGeom.Xform.Define(stage, "/World/Aircraft/Visual")
            scale_attribute = visual.GetPrim().GetAttribute("xformOp:scale")
            scale_op = UsdGeom.XformOp(scale_attribute) if scale_attribute else visual.AddScaleOp()
            scale = UsdGeom.GetStageMetersPerUnit(asset_stage)
            scale_op.Set(Gf.Vec3f(scale, scale, scale))
            reference = stage.DefinePrim("/World/Aircraft/Visual/Asset")
            reference.GetReferences().SetReferences([Sdf.Reference(str(asset_path))])

        chase = UsdGeom.Camera.Get(stage, "/World/ChaseCamera")
        if not chase:
            chase = UsdGeom.Camera.Define(stage, "/World/ChaseCamera")
            chase.CreateClippingRangeAttr(Gf.Vec2f(0.05, 20000))
            chase.CreateFocalLengthAttr(24)
        chase.ClearXformOpOrder()
        chase_transform = chase.AddTransformOp()
        onboard = UsdGeom.Camera.Get(stage, "/World/Aircraft/OnboardCamera")
        if not onboard:
            onboard = UsdGeom.Camera.Define(stage, "/World/Aircraft/OnboardCamera")
            onboard.CreateClippingRangeAttr(Gf.Vec2f(0.05, 20000))
            onboard.CreateFocalLengthAttr(18)
            onboard.AddTransformOp().Set(
                Gf.Matrix4d().SetLookAt(Gf.Vec3d(0.65, 0, 0.22), Gf.Vec3d(10, 0, 0.22), Gf.Vec3d(0, 0, 1)).GetInverse()
            )
    return translation, orientation, chase_transform


def run_view(receiver, args):
    renderers = {"realtime": "RealTimePathTracing", "minimal": "MinimalRendering", "legacy": "RaytracedLighting"}
    print(f"Starting Isaac Sim with the {args.renderer} renderer at {args.width}x{args.height}.", flush=True)
    print("First launch can spend several minutes compiling GPU shaders. Choose Wait if Ubuntu reports an unresponsive window.", flush=True)
    from isaacsim import SimulationApp

    app = SimulationApp({
        "headless": args.headless,
        "width": args.width,
        "height": args.height,
        "fast_shutdown": False,
        "renderer": renderers[args.renderer],
        "minimal_shading_mode": 2,
        "multi_gpu": False,
        "extra_args": ["--/log/channels/omni.rtx=info"],
    })
    try:
        import omni.usd
        from omni.kit.viewport.utility import get_active_viewport
        from pxr import Gf, Usd

        context = omni.usd.get_context()
        world_path = Path(args.world_usd).expanduser().resolve()
        if not context.open_stage(str(world_path)):
            raise RuntimeError(f"Cannot open world USD: {world_path}")
        app.reset_render_settings()
        if args.renderer == "minimal":
            app.set_setting("/rtx/sceneDb/ambientLightIntensity", 0.3)
        stage = context.get_stage()
        translation, orientation, chase_transform = prepare_scene(stage, args.aircraft_usd)
        print(f"World: {world_path}", flush=True)
        viewport = get_active_viewport()
        if viewport:
            viewport.camera_path = "/World/Aircraft/OnboardCamera" if args.camera == "onboard" else "/World/ChaseCamera"
        initial_frame = viewport.frame_info.get("frame_number") if viewport else None
        rendering = False

        deadline = time.monotonic() + args.wait_timeout
        connected = False
        previous_stale = False
        exit_requested = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: exit_requested.set())
        signal.signal(signal.SIGTERM, lambda *_: exit_requested.set())
        frame_period = 1.0 / args.fps
        print("Flight physics: PX4 SIH. Isaac Sim follows ground truth; scene collisions do not affect PX4.")
        print(f"Waiting for PX4 system {args.system_id} on {args.endpoint}")
        while app.is_running() and not exit_requested.is_set():
            started = time.monotonic()
            pose, count = receiver.snapshot()
            stale = pose is None or started - pose.received_at > 2.0
            if pose is not None:
                if not connected:
                    print(f"Connected: receiving HIL_STATE_QUATERNION ({count} packets)")
                    connected = True
                target = Gf.Vec3d(*pose.position) + Gf.Vec3d(0, 0, 0.25)
                rotation = Gf.Rotation(Gf.Quatd(pose.orientation[0], Gf.Vec3d(*pose.orientation[1:])))
                forward = rotation.TransformDir(Gf.Vec3d(1, 0, 0))
                horizontal = Gf.Vec3d(forward[0], forward[1], 0)
                if horizontal.GetLength() > 1e-6:
                    horizontal.Normalize()
                else:
                    horizontal = Gf.Vec3d(0, 1, 0)
                eye = target - horizontal * 6 + Gf.Vec3d(0, 0, 2.5)
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    translation.Set(Gf.Vec3d(*pose.position))
                    orientation.Set(Gf.Quatd(pose.orientation[0], Gf.Vec3d(*pose.orientation[1:])))
                    chase_transform.Set(Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse())
            if not connected and started > deadline:
                raise RuntimeError("PX4 ground-truth stream timed out. Start run_px4.sh, then restart the viewer.")
            if connected and stale != previous_stale:
                print("PX4 stream stopped; holding last pose" if stale else "PX4 stream resumed")
            previous_stale = stale
            app.update()
            if viewport and not rendering:
                frame = viewport.frame_info.get("frame_number")
                if frame is not None and frame != initial_frame:
                    print("Isaac viewport is rendering.", flush=True)
                    rendering = True
            time.sleep(max(0, frame_period - (time.monotonic() - started)))
    finally:
        app.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="udpin:127.0.0.1:19410")
    parser.add_argument("--system-id", type=int, default=1)
    parser.add_argument("--origin", type=float, nargs=3, default=(47.397742, 8.545594, 489.4), metavar=("LAT", "LON", "AMSL"))
    parser.add_argument("--check-link", action="store_true")
    parser.add_argument("--wait-timeout", type=float, default=120.0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--renderer", choices=("realtime", "minimal", "legacy"), default="realtime")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--camera", choices=("chase", "onboard"), default="chase")
    parser.add_argument("--world-usd", default=str(PROJECT_DIR / "worlds" / "runway.usda"))
    parser.add_argument("--aircraft-usd")
    parser.add_argument("--fps", type=float, default=30.0)
    args = parser.parse_args()
    if not 64 <= args.width <= 8192 or not 64 <= args.height <= 8192:
        parser.error("--width and --height must be between 64 and 8192")
    if not 1 <= args.system_id <= 255:
        parser.error("--system-id must be between 1 and 255")
    if not math.isfinite(args.fps) or args.fps <= 0:
        parser.error("--fps must be finite and positive")
    if not math.isfinite(args.wait_timeout) or args.wait_timeout <= 0:
        parser.error("--wait-timeout must be finite and positive")
    if not args.check_link:
        for asset in (args.world_usd, args.aircraft_usd):
            if asset and not Path(asset).expanduser().is_file():
                parser.error(f"USD file does not exist: {asset}")
    sys.argv = sys.argv[:1]
    receiver = MavlinkReceiver(args.endpoint, args.system_id, args.origin)
    try:
        if args.check_link:
            check_link(receiver, args.wait_timeout)
        else:
            run_view(receiver, args)
    finally:
        receiver.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
