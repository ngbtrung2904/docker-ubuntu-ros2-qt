"""Snap7 S7 server through ctypes, shared by monitoring_plc_asr.py and monitoring_plc_par.py.

Uses the libsnap7.so that rmm links (/usr/lib/libsnap7.so, in the container too), so no pip
package is needed. Set LIBSNAP7=<path> to use another copy.

The server answers rmm's connect, GetOrderCode/GetCpuInfo, DBRead and DBWrite by itself from
the registered data blocks. S7 values are big endian.
"""

import argparse
import ctypes
import os
import random
import socket
import struct
import threading
import time

SRV_AREA_DB = 5           # srvAreaDB in snap7.h
P_U16_LOCAL_PORT = 1      # p_u16_LocalPort
MK_EVENT = 0              # mkEvent
EVC_CLIENT_ADDED = 0x00000008
EVC_CLIENT_DISCONNECTED = 0x00000080
EVC_DATA_WRITE = 0x00040000


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class TSrvEvent(ctypes.Structure):
    _fields_ = [
        ("EvtTime", ctypes.c_long),     # time_t
        ("EvtSender", ctypes.c_int),    # client IPv4, network order
        ("EvtCode", ctypes.c_uint32),
        ("EvtRetCode", ctypes.c_uint16),
        ("EvtParam1", ctypes.c_uint16),  # data write: area
        ("EvtParam2", ctypes.c_uint16),  # data write: DB number
        ("EvtParam3", ctypes.c_uint16),  # data write: start byte
        ("EvtParam4", ctypes.c_uint16),  # data write: size
    ]


CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(TSrvEvent), ctypes.c_int)


class S7Server:
    def __init__(self, on_write=None):
        self.lib = ctypes.cdll.LoadLibrary(os.environ.get("LIBSNAP7", "libsnap7.so"))
        self.lib.Srv_Create.restype = ctypes.c_void_p
        self.handle = ctypes.c_void_p(self.lib.Srv_Create())
        self.buffers = {}
        self.lock = threading.Lock()
        self.on_write = on_write
        self._callback = CALLBACK(self._event)  # kept alive for as long as the server
        self.lib.Srv_SetEventsCallback(self.handle, self._callback, None)
        self.lib.Srv_SetMask(self.handle, MK_EVENT,
                             ctypes.c_uint32(EVC_CLIENT_ADDED | EVC_CLIENT_DISCONNECTED | EVC_DATA_WRITE))

    def register_db(self, number, size):
        buf = (ctypes.c_uint8 * size)()
        self.buffers[number] = buf
        self._check(self.lib.Srv_RegisterArea(self.handle, SRV_AREA_DB, number, ctypes.byref(buf), size),
                    f"register DB{number}")
        return buf

    def start(self, host, port):
        self._check(self.lib.Srv_SetParam(self.handle, P_U16_LOCAL_PORT, ctypes.byref(ctypes.c_uint16(port))),
                    "set port")
        self._check(self.lib.Srv_StartTo(self.handle, host.encode()), f"listen on {host}:{port}")

    def stop(self):
        self.lib.Srv_Stop(self.handle)
        self.lib.Srv_Destroy(ctypes.byref(self.handle))

    def read(self, db, offset, size):
        with self.lock:
            self.lib.Srv_LockArea(self.handle, SRV_AREA_DB, db)
            data = bytes(self.buffers[db][offset:offset + size])
            self.lib.Srv_UnlockArea(self.handle, SRV_AREA_DB, db)
        return data

    def write(self, db, offset, data):
        with self.lock:
            self.lib.Srv_LockArea(self.handle, SRV_AREA_DB, db)
            self.buffers[db][offset:offset + len(data)] = data
            self.lib.Srv_UnlockArea(self.handle, SRV_AREA_DB, db)

    def _check(self, ec, what):
        if ec != 0:
            text = ctypes.create_string_buffer(256)
            self.lib.Srv_ErrorText(ec, text, len(text))
            hint = " (port 102 needs root: run with sudo, or inside the container)" if what.startswith("listen") else ""
            raise RuntimeError(f"snap7 {what}: {text.value.decode()}{hint}")

    def _event(self, _usr, pevent, _size):
        e = pevent.contents
        peer = socket.inet_ntoa(struct.pack("<i", e.EvtSender)) if e.EvtSender else "?"
        if e.EvtCode == EVC_CLIENT_ADDED:
            log(f"rmm connected from {peer}")
        elif e.EvtCode == EVC_CLIENT_DISCONNECTED:
            log(f"rmm disconnected: {peer}")
        elif e.EvtCode == EVC_DATA_WRITE and e.EvtRetCode == 0 and self.on_write:
            try:
                self.on_write(peer, e.EvtParam2, e.EvtParam3, e.EvtParam4)
            except Exception as ex:  # an exception must not cross back into the C callback
                log(f"write handler failed: {ex}")


# ---------------------------------------------------------------------------
# Data block layout: fields are (name, kind, byte, bit-or-None, value)
# kind: "bool" one bit, "int" INT (int16), "real" REAL (float32), all big endian.
# ---------------------------------------------------------------------------
def bit(name, byte, bit_no, value=0):
    return (name, "bool", byte, bit_no, value)


def word(name, byte, value=0):
    return (name, "int", byte, None, value)


def real(name, byte, value=0.0):
    return (name, "real", byte, None, value)


def encode(fields, size):
    """Data block bytes for a field table."""
    data = bytearray(size)
    for _name, kind, byte, bit_no, value in fields:
        if kind == "bool":
            if value:
                data[byte] |= 1 << bit_no
            else:
                data[byte] &= ~(1 << bit_no) & 0xFF
        elif kind == "int":
            data[byte:byte + 2] = struct.pack(">h", int(value))
        else:
            data[byte:byte + 4] = struct.pack(">f", float(value))
    return bytes(data)


def decode(field, data):
    _name, kind, byte, bit_no, _value = field
    if kind == "bool":
        return (data[byte] >> bit_no) & 1
    if kind == "int":
        return struct.unpack(">h", data[byte:byte + 2])[0]
    return round(struct.unpack(">f", data[byte:byte + 4])[0], 3)


def field_bytes(field):
    _name, kind, byte, _bit, _value = field
    return range(byte, byte + {"bool": 1, "int": 2, "real": 4}[kind])


# ---------------------------------------------------------------------------
# Simulator shared by both variants
# ---------------------------------------------------------------------------
class PlcSimulator:
    """Serves a monitor DB (PLC -> PC) and a control DB (PC -> PLC) the way rmm uses them.

    random_ranges: name -> (lo, hi) for int/real fields in random mode; a bool not listed in
    random_fixed flips at random. pairs: (ok, error) bools, error drawn as the opposite of ok.
    """

    def __init__(self, mon_db, mon_size, mon_fields, ctl_db, ctl_size, ctl_fields,
                 random_ranges=None, random_fixed=(), pairs=()):
        self.mon_db, self.mon_size, self.mon_fields = mon_db, mon_size, list(mon_fields)
        self.ctl_db, self.ctl_size, self.ctl_fields = ctl_db, ctl_size, ctl_fields
        self.random_ranges = random_ranges or {}
        self.random_fixed = set(random_fixed)
        self.pairs = pairs
        self.server = S7Server(self.handle_write)
        self.server.register_db(mon_db, mon_size)
        self.server.register_db(ctl_db, ctl_size)
        self.server.write(mon_db, 0, encode(self.mon_fields, mon_size))
        self.ctl_last = self.server.read(ctl_db, 0, ctl_size)

    def dump(self):
        data = self.server.read(self.mon_db, 0, self.mon_size)
        log(f"DB{self.mon_db} (PLC -> rmm):")
        for f in self.mon_fields:
            log(f"  {f[2]:3d}{'.' + str(f[3]) if f[1] == 'bool' else '  '} {f[0]} = {decode(f, data)}")

    def draw(self):
        fields = []
        values = {}
        for name, kind, byte, bit_no, value in self.mon_fields:
            if name in self.random_ranges:
                lo, hi = self.random_ranges[name]
                value = random.uniform(lo, hi) if kind == "real" else random.randint(lo, hi)
            elif kind == "bool" and name not in self.random_fixed:
                value = random.randint(0, 1)
            values[name] = value
            fields.append((name, kind, byte, bit_no, value))
        for ok, err in self.pairs:
            values[err] = 0 if values[ok] else 1
        fields = [(n, k, b, bt, values[n]) for n, k, b, bt, _ in fields]
        self.mon_fields = fields
        self.server.write(self.mon_db, 0, encode(fields, self.mon_size))
        self.dump()

    def handle_write(self, peer, db, start, size):
        if db != self.ctl_db:
            log(f"<- {peer} wrote DB{db} bytes {start}..{start + size - 1}: not a control DB, kept as written")
            return
        data = self.server.read(self.ctl_db, 0, self.ctl_size)
        touched = set(range(start, start + size))
        changes = [f"{f[0]} {decode(f, self.ctl_last)} -> {decode(f, data)}" for f in self.ctl_fields
                   if touched.intersection(field_bytes(f)) and decode(f, data) != decode(f, self.ctl_last)]
        if not changes:
            old, new = self.ctl_last[start:start + size].hex(), data[start:start + size].hex()
            changes = [f"no named field, bytes {old} -> {new}" if old != new else "no change"]
        self.ctl_last = data
        log(f"<- {peer} DB{db}[{start}:{start + size}]: " + "; ".join(changes))


def run(description, make_sim, doc):
    ap = argparse.ArgumentParser(description=description, epilog=doc,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=102, help="S7 port; rmm always connects to 102 (default)")
    ap.add_argument("--random", type=float, default=0, metavar="N",
                    help="draw new random monitor values every N seconds (default: fixed table values)")
    args = ap.parse_args()
    if args.random < 0:
        ap.error("--random must be positive")

    sim = make_sim()
    try:
        sim.server.start(args.host, args.port)
    except RuntimeError as e:
        log(str(e))
        raise SystemExit(1)
    log(f"S7 PLC listening on {args.host}:{args.port}: rmm reads DB{sim.mon_db} ({sim.mon_size} bytes), "
        f"writes DB{sim.ctl_db} ({sim.ctl_size} bytes)")
    if args.random:
        log(f"random mode: new values every {args.random:g} s")
        sim.draw()
    else:
        log("default mode: fixed table values; rmm writes are logged and stored in the control DB")
        sim.dump()
    try:
        while True:
            time.sleep(args.random or 3600)
            if args.random:
                sim.draw()
    except KeyboardInterrupt:
        log("Stopped")
    finally:
        sim.server.stop()
