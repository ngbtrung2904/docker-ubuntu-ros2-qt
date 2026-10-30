#!/usr/bin/env python3
"""Fake ASR SPC device: streams encoder, video, hit and meteo frames on TCP.

rdp connects to it exactly as it connects to the real SPC (SpcDevAddress in
ASR rdp src/appconfig.ini, default 127.0.0.1:8889) and forwards every frame
unchanged to rdd on PortPublishRData (60001).

    python3 tools/sim_spc_rdata.py                     # terrain/meteo map + moving targets
    python3 tools/sim_spc_rdata.py --mode calib        # fixed test pattern
    python3 tools/sim_spc_rdata.py --types video,meteo # leave hits out

Frame layout (little endian), same as the real SPC and as both RDataProcessing
decoders expect:
    SOP 0x0D0D | SEQ u16 | SRC_ID u8 | TYPE u8 | LEN u16 | DATA | CHKSUM u16 | EOP 0x0A0A
    LEN is the whole frame; CHKSUM is the XOR of every byte before it (high byte 0).
    0x00 encoder : ENC u16, ENC u16, TIME u32, ENC_BIT u8, 7 reserved     (16 bytes)
    0x01 video   : LOC u16, N u16, CHANNEL u8, COMPRESS u8, ENC u16, ENC_BIT u8,
    0x04 meteo     FREQ u8, TIME u32, RESERVED u32, N x POWER u32
    0x02 hit     : per hit LOC u16, CHANNEL u8, ENC u16, DOPPLER i16, FREQ u8,
                   MERIT u8, ENC_BIT u8, TIME u32, RESERVED u32, POWER u32  (22 bytes)
Defaults (source id 0x34, 16-bit encoder advancing 22 counts per spoke,
5480 range cells in chunks of 300, 37.5 m cells) copy the ASR recording
rrecord/asr_20260512_1233.

Azimuths below are encoder azimuths (0 = encoder zero, clockwise). rdd adds
NorthOffsetAngle from its appconfig.ini before drawing, so the picture on the
scope is rotated by that amount.

How rdd draws each type (GLRenderArea::updateColorData):
    video: green = (2*P - 1) / 2047, full green from P of about 1024.
    meteo: colour step = int((2*P - 1) / 2047 * 64), clamped to 0..63, drawn
           only where meteo power >= video power.
    hit:   fixed yellow on one display cell (0.35 deg x 2 range cells); the
           power field is not used, so a hit shows larger only by covering more
           cells (--hit-cells, --hit-width-deg).

Scene mode sends one fixed clutter map (mountain ranges and near-range ground)
as both RAW video and meteo, identical on every scan and, for the same
--clutter-seed, on every run. Equal power makes rdd draw the meteo colours over
those shapes when Meteo is on, and green RAW when it is off. Targets add power to
the video only, so they stay green on top of the meteo.
"""

import argparse
import math
import socket
import struct
import sys
import threading
import time

import numpy as np

SOP = 0x0D0D
EOP = 0x0A0A
MSG_ENCODER = 0x00
MSG_VIDEO = 0x01
MSG_HIT = 0x02
MSG_METEO = 0x04
ENC_BIT = 16
ENC_SIZE = 1 << ENC_BIT

HIT_DTYPE = np.dtype([
    ("loc", "<u2"), ("channel", "u1"), ("enc", "<u2"), ("doppler", "<i2"),
    ("freq", "u1"), ("merit", "u1"), ("enc_bit", "u1"), ("time", "<u4"),
    ("reserved", "<u4"), ("power", "<u4"),
])
assert HIT_DTYPE.itemsize == 22


def xor_bytes(buf):
    return int(np.bitwise_xor.reduce(np.frombuffer(buf, dtype=np.uint8))) if len(buf) else 0


def xor_u32(arr):
    """XOR of the little-endian bytes of a uint32 array, without converting to bytes."""
    if arr.size == 0:
        return 0
    x = int(np.bitwise_xor.reduce(arr))
    return (x ^ (x >> 8) ^ (x >> 16) ^ (x >> 24)) & 0xFF


def now_ms():
    return int(time.time() * 1000) & 0xFFFFFFFF

def angle_diff_deg(a, b):
    """Signed a - b in degrees, wrapped to [-180, 180)."""
    return (a - b + 180.0) % 360.0 - 180.0


def ramp_power(index):
    """Meteo power that lands in the middle of colour step `index` on rdd."""
    return int(round((1.0 + 2047.0 * (index + 0.5) / 64.0) / 2.0))


class FrameBuilder:
    def __init__(self, source_id, chunk):
        self.source_id = source_id
        self.chunk = chunk
        self.seq = {}

    def _next_seq(self, msg_type):
        s = self.seq.get(msg_type, 0)
        self.seq[msg_type] = (s + 1) & 0xFFFF
        return s

    def frame(self, msg_type, payload, payload_xor=None):
        length = 8 + len(payload) + 4
        if length > 0xFFFF:
            raise ValueError("frame too long: %d" % length)
        head = struct.pack("<HHBBH", SOP, self._next_seq(msg_type), self.source_id, msg_type, length)
        if payload_xor is None:
            payload_xor = xor_bytes(payload)
        chk = xor_bytes(head) ^ payload_xor
        return b"".join((head, payload, struct.pack("<HH", chk, EOP)))

    def encoder(self, enc):
        # The real SPC repeats ENC in the reserved word and sends ENC_BIT 0.
        payload = struct.pack("<HHIB7x", enc, enc, now_ms(), 0)
        return self.frame(MSG_ENCODER, payload)

    def spoke(self, msg_type, enc, powers):
        """Video or meteo spoke, split into chunks like the real SPC."""
        frames = []
        t = now_ms()
        n_cells = powers.size
        for loc in range(0, n_cells, self.chunk):
            part = powers[loc:loc + self.chunk]
            head = struct.pack("<HHBBHBBII", loc, part.size, 0, 1, enc, ENC_BIT, 0, t, 0)
            frames.append(self.frame(msg_type, head + part.tobytes(), xor_bytes(head) ^ xor_u32(part)))
        return frames

    def hits(self, enc, hit_list):
        """hit_list: iterable of (range cell, power, doppler)."""
        if not hit_list:
            return []
        arr = np.zeros(len(hit_list), dtype=HIT_DTYPE)
        t = now_ms()
        for i, (loc, power, doppler) in enumerate(hit_list):
            arr[i] = (loc, 0, enc, doppler, 0, 0, ENC_BIT, t, 0, power)
        # Keep each frame well under the 16-bit LEN limit.
        per_frame = 2000
        return [self.frame(MSG_HIT, arr[i:i + per_frame].tobytes())
                for i in range(0, len(arr), per_frame)]


def value_noise(x, y, cell_km, extent_km, rng):
    """Smooth random field in [0, 1] at points (x, y) km: a random grid every cell_km, bilinear."""
    n = int(2 * extent_km / cell_km) + 3
    grid = rng.random((n, n), dtype=np.float32)
    gx = (x + extent_km) / cell_km
    gy = (y + extent_km) / cell_km
    ix = np.clip(np.floor(gx).astype(np.int32), 0, n - 2)
    iy = np.clip(np.floor(gy).astype(np.int32), 0, n - 2)
    fx = gx - ix
    fy = gy - iy
    fx = fx * fx * (3 - 2 * fx)
    fy = fy * fy * (3 - 2 * fy)
    top = grid[iy, ix] * (1 - fx) + grid[iy, ix + 1] * fx
    bottom = grid[iy + 1, ix] * (1 - fx) + grid[iy + 1, ix + 1] * fx
    return top * (1 - fy) + bottom * fy


class ClutterMap:
    """Fixed ground clutter (mountain ranges + near-range ground), the same on every scan.

    Built once on rdd's display grid (1024 azimuth bins, 2 range cells per column)
    from its own seed, so the map is also the same from run to run. Each range is
    a wandering chain of peaks, its edges roughened by fractal noise and its
    inside given a fixed grain, like real terrain returns.
    """

    AZ_BINS = 1024
    PEAK = 1400.0      # rdd: full green from about 1024, meteo red from about 1008

    def __init__(self, args):
        rng = np.random.default_rng(args.clutter_seed)
        res_km = args.range_res_m / 1000.0
        cols = (args.cells + 1) // 2
        extent = cols * 2 * res_km + 20.0
        az = (np.arange(self.AZ_BINS, dtype=np.float32) + 0.5) * (2 * np.pi / self.AZ_BINS)
        r = (np.arange(cols, dtype=np.float32) + 0.5) * (2 * res_km)
        x = np.sin(az)[:, None] * r[None, :]
        y = np.cos(az)[:, None] * r[None, :]

        def fbm(octaves):
            acc = np.zeros_like(x)
            total = 0.0
            for cell_km, amp in octaves:
                acc += amp * value_noise(x, y, cell_km, extent, rng)
                total += amp
            return acc / total

        # Warp the coordinates so the ranges bend instead of being ellipses.
        wx = x + 10.0 * (fbm(((20.0, 1.0), (8.0, 0.5))) - 0.5)
        wy = y + 10.0 * (fbm(((20.0, 1.0), (8.0, 0.5))) - 0.5)
        rough = fbm(((10.0, 1.0), (5.0, 0.6), (2.5, 0.4), (1.2, 0.3), (0.6, 0.2)))

        ranges = np.zeros_like(x)
        for _ in range(args.ridges):
            ra = rng.uniform(0, 2 * np.pi)
            rr = rng.uniform(25, min(170.0, extent - 40))
            px, py = rr * math.sin(ra), rr * math.cos(ra)
            heading = rng.uniform(0, 2 * np.pi)
            for _ in range(int(rng.integers(5, 10))):
                la, lc = rng.uniform(6, 11), rng.uniform(4, 7)
                u = (wx - px) * math.cos(heading) + (wy - py) * math.sin(heading)
                v = -(wx - px) * math.sin(heading) + (wy - py) * math.cos(heading)
                peak = rng.uniform(0.7, 1.0)
                ranges = np.maximum(ranges, peak * np.exp(-(u * u / (2 * la * la) + v * v / (2 * lc * lc))))
                step = rng.uniform(4, 8)
                px += step * math.cos(heading)
                py += step * math.sin(heading)
                heading += rng.uniform(-0.6, 0.6)

        grain = rng.uniform(0.9, 1.0, size=x.shape).astype(np.float32)   # fixed speckle
        height = ranges * (0.35 + 0.65 * rough)
        mountains = self.PEAK * np.clip((height - 0.12) / 0.75, 0, 1) ** 1.3
        ground = 1300.0 * np.exp(-r / 6.0)[None, :] * np.clip(1.6 * rough - 0.35, 0, 1)
        power = np.maximum(mountains, ground) * grain
        power[power < 60] = 0
        self.map = np.repeat(power.astype(np.uint16), 2, axis=1)[:, :args.cells]

    def spoke(self, az_deg):
        return self.map[int(az_deg / 360.0 * self.AZ_BINS) % self.AZ_BINS]


class SceneModel:
    """Fixed clutter map sent as both RAW video and meteo, plus moving targets with hits."""

    BEAM_DEG = 1.2     # half width where a target paints video

    def __init__(self, args, rng):
        self.rng = rng
        self.res_km = args.range_res_m / 1000.0
        self.cells = args.cells
        self.r_km = (np.arange(self.cells) + 0.5) * self.res_km
        self.max_km = self.cells * self.res_km
        self.noise = args.noise
        self.hit_cells = args.hit_cells
        self.hit_deg = args.hit_width_deg
        self.hit_power = args.hit_power

        t0 = time.monotonic()
        self.clutter = ClutterMap(args)
        print("clutter map: %d ridges, seed %d, built in %.1f s"
              % (args.ridges, args.clutter_seed, time.monotonic() - t0), flush=True)

        self.targets = [self._new_target() for _ in range(args.targets)]

    def _new_target(self):
        rng = self.rng
        az = rng.uniform(0, 2 * math.pi)
        r = rng.uniform(15, min(150.0, self.max_km - 20))
        heading = rng.uniform(0, 2 * math.pi)
        speed = rng.uniform(0.06, 0.25)   # km/s
        return [r * math.sin(az), r * math.cos(az),
                speed * math.sin(heading), speed * math.cos(heading),
                rng.uniform(1500, 2000)]

    def advance(self, dt):
        for i, t in enumerate(self.targets):
            t[0] += t[2] * dt
            t[1] += t[3] * dt
            r = math.hypot(t[0], t[1])
            if r < 5 or r > self.max_km - 10:
                self.targets[i] = self._new_target()

    def spoke(self, az_deg):
        rng = self.rng
        ground = self.clutter.spoke(az_deg).astype(np.float64)
        if self.noise > 0:
            ground += rng.exponential(self.noise, self.cells)
        # Meteo is the clutter itself; targets are added to video only, so they
        # stay green on top of the meteo colours.
        meteo = ground.copy()
        video = ground
        hits = []
        for x, y, vx, vy, amp in self.targets:
            t_az = math.degrees(math.atan2(x, y)) % 360.0
            d = angle_diff_deg(az_deg, t_az)
            if abs(d) > max(self.BEAM_DEG, self.hit_deg):
                continue
            r = math.hypot(x, y)
            rc = r / self.res_km
            lo, hi = max(0, int(rc) - 8), min(self.cells, int(rc) + 9)
            idx = np.arange(lo, hi)
            video[lo:hi] += amp * math.exp(-(d / 0.6) ** 2) * np.exp(-((idx - rc) / 3.0) ** 2)
            if abs(d) <= self.hit_deg:
                radial_ms = max(-32768, min(32767, int((vx * x + vy * y) / r * 1000.0)))
                for loc in range(int(rc) - self.hit_cells, int(rc) + self.hit_cells + 1):
                    if 0 <= loc < self.cells:
                        hits.append((loc, self.hit_power, radial_ms))

        return (np.minimum(video, 4095).astype(np.uint32),
                np.minimum(meteo, 4095).astype(np.uint32),
                hits)


class CalibModel:
    """Static pattern with known values, one sector per data type.

    encoder az   0-120 : meteo, 64 range bands, band k in the middle of colour step k
    encoder az 120-240 : video, 8 range bands, power 256, 512, ... 2048
    encoder az 240-360 : hits every 10 km along 270 and 315 degrees
    """

    HIT_AZ = (270.0, 315.0)
    HIT_DEG = 0.5

    def __init__(self, args):
        self.cells = args.cells
        res_km = args.range_res_m / 1000.0
        shown = min(args.cells, args.display_cells)

        self.band_cells = shown // 64
        self.meteo = np.zeros(self.cells, dtype=np.uint32)
        for k in range(64):
            self.meteo[k * self.band_cells:(k + 1) * self.band_cells] = ramp_power(k)

        step = shown // 8
        self.video = np.zeros(self.cells, dtype=np.uint32)
        for j in range(8):
            self.video[j * step:(j + 1) * step] = 256 * (j + 1)

        self.hit_cells = [int(km / res_km) for km in range(10, int(shown * res_km), 10)]
        self.zero = np.zeros(self.cells, dtype=np.uint32)

        print("calib: meteo ramp at encoder az 0-120, %d cells (%.2f km) per colour step"
              % (self.band_cells, self.band_cells * res_km))
        for k in (0, 8, 16, 24, 32, 40, 48, 63):
            print("  step %2d  cells %4d-%4d  %6.1f-%6.1f km  power %4d"
                  % (k, k * self.band_cells, (k + 1) * self.band_cells - 1,
                     k * self.band_cells * res_km, (k + 1) * self.band_cells * res_km, ramp_power(k)))
        print("calib: video bands 256..2048 at az 120-240, %d cells each; hits every 10 km at az %s"
              % (step, "/".join("%g" % a for a in self.HIT_AZ)))

    def advance(self, dt):
        pass

    def spoke(self, az_deg):
        if az_deg < 120.0:
            return self.zero, self.meteo, []
        if az_deg < 240.0:
            return self.video, self.zero, []
        hits = []
        if any(abs(angle_diff_deg(az_deg, a)) <= self.HIT_DEG for a in self.HIT_AZ):
            hits = [(c, 3000, 0) for loc in self.hit_cells
                    for c in range(loc - 4, loc + 5) if 0 <= c < self.cells]
        return self.zero, self.zero, hits


class Server:
    def __init__(self, host, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(4)
        self.clients = []
        self.lock = threading.Lock()
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            conn, addr = self.sock.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print("client connected: %s:%d" % addr, flush=True)
            with self.lock:
                self.clients.append((conn, addr))

    def has_clients(self):
        with self.lock:
            return bool(self.clients)

    def send(self, data):
        with self.lock:
            clients = list(self.clients)
        for conn, addr in clients:
            try:
                conn.sendall(data)
            except OSError as ex:
                print("client %s:%d dropped: %s" % (addr[0], addr[1], ex), flush=True)
                conn.close()
                with self.lock:
                    self.clients = [c for c in self.clients if c[0] is not conn]


def parse_args():
    p = argparse.ArgumentParser(description="Fake ASR SPC: encoder, video, hit and meteo frames for rdp.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8889, help="rdp SpcDevAddress port (default 8889)")
    p.add_argument("--mode", choices=("scene", "calib"), default="scene")
    p.add_argument("--types", default="video,hit,meteo",
                   help="comma list of video, hit, meteo to send (encoder is always sent)")
    p.add_argument("--rpm", type=float, default=12.0, help="antenna speed (default 12)")
    p.add_argument("--enc-step", type=int, default=22,
                   help="encoder counts per spoke, 16-bit encoder (default 22, as the real SPC)")
    p.add_argument("--cells", type=int, default=5480, help="range cells per spoke (default 5480)")
    p.add_argument("--chunk", type=int, default=300, help="range cells per video/meteo frame (default 300)")
    p.add_argument("--range-res-m", type=float, default=37.5, help="range cell size (default 37.5 m)")
    p.add_argument("--display-cells", type=int, default=5280,
                   help="range cells rdd keeps (RDataMgmt rangeSize, default 5280); calib fits in it")
    p.add_argument("--targets", type=int, default=6)
    p.add_argument("--ridges", type=int, default=7, help="mountain ridges in the clutter map (default 7)")
    p.add_argument("--clutter-seed", type=int, default=2025,
                   help="seed of the fixed clutter map; same seed, same map (default 2025)")
    p.add_argument("--noise", type=float, default=0.0,
                   help="mean of random receiver noise added to video each scan (default 0: none)")
    p.add_argument("--hit-cells", type=int, default=12,
                   help="hits on each side of a target's range cell (default 12: 25 cells, about 940 m)")
    p.add_argument("--hit-width-deg", type=float, default=1.2,
                   help="half azimuth width where a target produces hits (default 1.2)")
    p.add_argument("--hit-power", type=int, default=3000, help="power field of every hit (default 3000)")
    p.add_argument("--source-id", type=lambda v: int(v, 0), default=0x34)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()
    args.types = {t.strip() for t in args.types.split(",") if t.strip()}
    unknown = args.types - {"video", "hit", "meteo"}
    if unknown:
        p.error("unknown --types: %s" % ", ".join(sorted(unknown)))
    if not 0 < args.enc_step < ENC_SIZE or args.rpm <= 0 or not 0 < args.chunk <= 16000:
        p.error("--enc-step, --rpm or --chunk out of range")
    return args


def main():
    args = parse_args()
    rng = np.random.default_rng(args.seed)
    model = CalibModel(args) if args.mode == "calib" else SceneModel(args, rng)
    builder = FrameBuilder(args.source_id, args.chunk)
    server = Server(args.host, args.port)

    spokes_per_rev = ENC_SIZE / args.enc_step
    spoke_period = 60.0 / args.rpm / spokes_per_rev
    print("listening on %s:%d, mode %s, types %s, %.1f rpm, %d spokes/rev, %.2f ms/spoke"
          % (args.host, args.port, args.mode, ",".join(sorted(args.types)),
             args.rpm, round(spokes_per_rev), spoke_period * 1000), flush=True)

    enc = 0
    next_time = time.monotonic()
    stat_time, stat_bytes, stat_spokes = next_time, 0, 0
    batch, batch_start = [], next_time
    while True:
        az = enc * 360.0 / ENC_SIZE
        model.advance(spoke_period)
        if server.has_clients():
            video, meteo, hits = model.spoke(az)
            batch.append(builder.encoder(enc))
            if "video" in args.types:
                batch.extend(builder.spoke(MSG_VIDEO, enc, video))
            if "meteo" in args.types:
                batch.extend(builder.spoke(MSG_METEO, enc, meteo))
            if "hit" in args.types:
                batch.extend(builder.hits(enc, hits))
        stat_spokes += 1
        enc = (enc + args.enc_step) % ENC_SIZE
        next_time += spoke_period

        now = time.monotonic()
        if now - batch_start >= 0.01 or next_time - now > 0.002:
            if batch:
                data = b"".join(batch)
                stat_bytes += len(data)
                server.send(data)
                batch = []
            batch_start = now
            delay = next_time - time.monotonic()
            if delay > 0:
                time.sleep(delay)

        if now - stat_time >= 5.0:
            lag = now - next_time
            print("%5.0f spokes/s  %6.2f MB/s  lag %.2f s%s"
                  % (stat_spokes / (now - stat_time), stat_bytes / (now - stat_time) / 1e6, max(lag, 0.0),
                     "  (falling behind: raise --enc-step or lower --rpm)" if lag > 1.0 else ""),
                  flush=True)
            stat_time, stat_bytes, stat_spokes = now, 0, 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
