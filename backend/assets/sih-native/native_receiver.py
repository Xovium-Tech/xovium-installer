import ctypes as C
from pathlib import Path
from types import SimpleNamespace


class NativePose(C.Structure):
    _fields_ = [("position", C.c_double * 3), ("orientation", C.c_double * 4),
                ("received_at", C.c_double), ("count", C.c_uint64)]


class MavlinkReceiver:
    def __init__(self, endpoint, system_id, origin):
        parts = endpoint.split(":")
        if len(parts) != 3 or parts[0] != "udpin":
            raise ValueError("Native SIH expects udpin:IPv4:port")
        port = int(parts[2])
        if not 1 <= port <= 65535 or not 1 <= system_id <= 255:
            raise ValueError("Invalid UDP port or MAVLink system id")
        library = Path(__file__).resolve().parent / "build/libxovium_sih.so"
        if not library.is_file():
            raise RuntimeError("Native SIH receiver is missing. Run ./install.sh --refresh-launchers.")
        self.lib = C.CDLL(str(library))
        self.lib.sih_open.argtypes = [C.c_char_p, C.c_uint16, C.c_uint8, C.POINTER(C.c_double), C.c_char_p, C.c_size_t]
        self.lib.sih_open.restype = C.c_void_p
        self.lib.sih_snapshot.argtypes = [C.c_void_p, C.POINTER(NativePose), C.c_char_p, C.c_size_t]
        self.lib.sih_snapshot.restype = C.c_int
        self.lib.sih_close.argtypes = [C.c_void_p]
        self.lib.sih_close.restype = None
        error = C.create_string_buffer(512)
        address = "127.0.0.1" if parts[1] == "localhost" else parts[1]
        self.handle = self.lib.sih_open(address.encode(), port, system_id, (C.c_double * 3)(*origin), error, len(error))
        if not self.handle:
            raise RuntimeError(error.value.decode())

    def snapshot(self):
        pose = NativePose()
        error = C.create_string_buffer(512)
        result = self.lib.sih_snapshot(self.handle, C.byref(pose), error, len(error))
        if result < 0:
            raise RuntimeError(error.value.decode())
        if result == 0:
            return None, 0
        return SimpleNamespace(position=tuple(pose.position), orientation=tuple(pose.orientation),
                               received_at=pose.received_at), pose.count

    def close(self):
        if self.handle:
            self.lib.sih_close(self.handle)
            self.handle = None
