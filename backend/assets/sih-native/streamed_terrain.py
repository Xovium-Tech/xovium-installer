"""Display the native Terrarium stream without introducing a second terrain model."""
import json
import math
import os
from pathlib import Path
import shlex
import socket
import subprocess


def read_tile(path):
    lines = Path(path).read_text().splitlines()
    texture = next(shlex.split(line)[1] for line in lines if line.startswith("texture "))
    start = next(i for i, line in enumerate(lines) if line.startswith("numvert "))
    count = int(lines[start].split()[1])
    points = []
    for line in lines[start + 1:start + 1 + count]:
        east, up, negative_north = map(float, line.split())
        points.append((east, -negative_north, up))
    indices, counts, uvs = [], [], []
    for i, line in enumerate(lines):
        if line.startswith("refs "):
            count = int(line.split()[1])
            counts.append(count)
            for row in lines[i + 1:i + 1 + count]:
                vertex, u, v = row.split()
                indices.append(int(vertex))
                uvs.append((float(u), float(v)))
    if not points or not indices or any(index < 0 or index >= len(points) for index in indices):
        raise ValueError("Invalid native terrain mesh export")
    return points, counts, indices, uvs, texture


class StreamedTerrain:
    def __init__(self, stage, instance, origin):
        self.stage, self.origin = stage, origin
        self.process = None
        self.socket = None
        self.loaded = set()
        self.last_modified = None
        bridge = Path(__file__).resolve().parent
        config = os.environ["XOVIUM_TERRAIN_CONFIG"]
        if os.environ.get("XOVIUM_VEHICLE") == "r1_rover":
            runtime = Path(os.environ.get("PX4_RUNTIME", bridge / ".runtime" / f"sih-rover-{instance}"))
            self.export = Path(os.environ.get("XOVIUM_TERRAIN_EXPORT", runtime / "terrain"))
        else:
            runtime = bridge / ".runtime" / f"sih-view-{instance}"
            runtime.mkdir(parents=True, exist_ok=True)
            self.export = runtime / "terrain"
            port = 16700 + instance
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.connect(("127.0.0.1", port))
            self.process = subprocess.Popen([str(bridge / "build/xovium_rover"), "--terrain-only",
                "--terrain-config", config, "--terrain-export", str(self.export), "--fg-position-port", str(port),
                "--origin", *map(str, origin)])

    def update(self, position=None):
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        if self.process and self.process.poll() is not None:
            raise RuntimeError("Dynamic terrain streamer stopped; see its error above")
        if self.socket and position is not None:
            earth = 6378137.0
            latitude = self.origin[0] + math.degrees(position[1] / earth)
            longitude = self.origin[1] + math.degrees(position[0] / (earth * math.cos(math.radians(self.origin[0]))))
            try:
                self.socket.send(f"{latitude:.10f},{longitude:.10f}\n".encode())
            except ConnectionRefusedError:
                pass # The native streamer's UDP endpoint is still starting.
        manifest = self.export / "scene.json"
        if not manifest.is_file():
            return
        modified = manifest.stat().st_mtime_ns
        if modified == self.last_modified:
            return
        document = json.loads(manifest.read_text())
        if document.get("schema") != 1:
            raise ValueError("Unsupported native terrain scene schema")
        current = {item["key"]: item["path"] for item in document["models"]}
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            for key in self.loaded - current.keys():
                self.stage.RemovePrim("/World/SharedTerrain/tile_" + key.removesuffix(".ac").replace("-", "_"))
            for key, path in current.items():
                if key in self.loaded:
                    continue
                name = "/World/SharedTerrain/tile_" + key.removesuffix(".ac").replace("-", "_")
                points, counts, indices, uvs, image = read_tile(path)
                mesh = UsdGeom.Mesh.Define(self.stage, name)
                mesh.CreatePointsAttr([Gf.Vec3f(*point) for point in points])
                mesh.CreateFaceVertexCountsAttr(counts)
                mesh.CreateFaceVertexIndicesAttr(indices)
                mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
                mesh.CreateDoubleSidedAttr(True)
                UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                    UsdGeom.Tokens.faceVarying).Set([Gf.Vec2f(*uv) for uv in uvs])
                material = UsdShade.Material.Define(self.stage, name + "/Material")
                surface = UsdShade.Shader.Define(self.stage, name + "/Material/Surface")
                surface.CreateIdAttr("UsdPreviewSurface")
                surface.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
                reader = UsdShade.Shader.Define(self.stage, name + "/Material/UV")
                reader.CreateIdAttr("UsdPrimvarReader_float2")
                reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
                texture = UsdShade.Shader.Define(self.stage, name + "/Material/Texture")
                texture.CreateIdAttr("UsdUVTexture")
                texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(str(Path(image).resolve()))
                texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
                texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
                surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture.ConnectableAPI(), "rgb")
                material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
                UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        self.loaded = set(current)
        self.last_modified = modified

    def close(self):
        if self.socket:
            self.socket.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
