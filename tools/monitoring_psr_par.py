#!/usr/bin/env python3
"""PSR simulation for PAR via TCP protocol

PAR rmm opens one TCP client per reporter (configuration.xml):
    DspDevAddress  ->  TX(1) RX(2) SYNC(3) RFMON(4) DSP(6)
    SpcDevAddress  ->  SPC(52)
and routes every frame by its SRC id, whichever connection it arrives on. This script serves
all six devices on one port and sends every device on every connection, so with both addresses
pointing here rmm decodes each device twice per status cycle.

Payload
    SOP 0x0D | SRC | DST | LEN u16 LE (whole frame) | DATA | XOR of all previous bytes | EOP 0x0A
    DATA = first register number, then for each register: [size - 1][value, little endian]

The register sizes below follow each device's decodeSubMessage(). rmm silently drops a
register whose size differs from what the decoder expects. Values are raw: rmm multiplies
them by the <step> in configuration.xml (0.1 where noted "x0.1"). Registers with no decoder
("not decoded") keep the size the old tools/monitor_spc_dsp_par.py sent.

Two working modes:

default     Sends the table values below every second, like tools/monitor_spc_dsp_par.py.
            Write commands from rmm (a control changed in rdd) are logged and ignored, so the
            control goes back to the table value on the next status frame.

--random N  Draws new values for every register every N seconds (see FLAGS, CHOICES, RANGES):
            status bits and on/off controls flip between their two states, selectors take one
            of the values rdd offers, measurements take a plausible value for a PAR system.
            RFMON Tx_Power stays below rdd's 700 W warning limit for 40 s, then above it for
            5 s, and repeats, whatever N is (a new value inside the phase's range each draw).
            A write from rmm is applied and reported until the next draw replaces it.

Every register is printed at start-up, and again after each random draw.

rdd's RfMonModel shows the largest Tx_Power of the last 20 RFMON frames (BUFFER_SIZE). rmm
publishes once per frame, and with both rmm addresses pointing here it gets RFMON twice a
second, so after the high phase rdd keeps showing a high value for about 10 s more. The
warning then shows for the remaining ~30 s of the low phase: about two thirds of the time.

Listens on 0.0.0.0:8888 and sends a status frame for every device every 1 s.

Usage:
    python3 tools/monitoring_psr_par.py                   # fixed values
    python3 tools/monitoring_psr_par.py --random 30       # new random values every 30 s

Point rmm at it in its appconfig.ini, [Device] section:
    DspDevAddress=127.0.0.1:8888
    SpcDevAddress=127.0.0.1:8888
"""

import argparse
import random
import socket
import threading
import time
from functools import reduce
from operator import xor

SOP = 0x0D
EOP = 0x0A
CMD_READ = 0x01
CMD_WRITE = 0x02
RMM_ID = 53  # rmm appconfig.ini MyID: DST of every status frame
HOST = "0.0.0.0"
PORT = 8888      # rmm DspDevAddress and SpcDevAddress (8889 is the SPC radar stream rdp reads)
INTERVAL = 1.0   # seconds between status cycles

# ---------------------------------------------------------------------------
# Register tables: (name, size in bytes, raw value); list index = register number.
# Register 0 is the project/software/hardware version, which rmm does not decode.
# ---------------------------------------------------------------------------
VERSION = ("Version", 3, 0x123456)

SYNC_REGS = [
    VERSION,
    ("Temp_Board",        2, 45),   # degC
    ("Humidity",          2, 40),   # %
    ("Temp_SOM",          2, 52),   # degC
    ("CurrPSU_24Vin",     2, 85),   # x0.1 A
    ("CurrPSU_12Vout",    2, 60),   # x0.1 A
    ("CurrPSU_5Vout",     2, 40),   # x0.1 A
    ("CurrPSU_3P3Vout",   2, 25),   # x0.1 A
    ("VoltPSU_12Vout",    2, 120),  # x0.1 V
    ("VoltPSU_5Vout",     2, 50),   # x0.1 V
    ("VoltPSU_3P3Vout",   2, 33),   # x0.1 V
    ("Curr_12V_sup",      2, 15),   # x0.1 A
    ("Curr_5V_sup",       2, 20),   # x0.1 A
    ("Curr_3P3V_sup",     2, 8),    # x0.1 A
    ("Volt_12V_sup",      2, 120),  # x0.1 V
    ("Volt_5V_sup",       2, 50),   # x0.1 V
    ("Volt_3P3V_sup",     2, 33),   # x0.1 V
    ("HorizontalEncoder", 2, 0),    # int16, rdd shows raw * 180 / 32768 deg
    ("VerticalEncoder",   2, 0),    # int16, rdd shows raw * 180 / 32768 deg
    ("Ctrl_bus_err",      2, 0),    # not decoded
    ("Mon_bus_err",       2, 0),    # not decoded
]

# SUPPLY common for TX & RX
SUPPLY_REGS = [
    VERSION,
    ("Temp_Board",     2, 45),      # degC
    ("Humidity",       2, 40),      # %
    ("Temp_SOM",       2, 52),      # degC
    ("Curr_12V_sup",   2, 15),      # x0.1 A
    ("Curr_5V_sup",    2, 20),      # x0.1 A
    ("Curr_3P3V_sup",  2, 8),       # x0.1 A
    ("Volt_12V_sup",   2, 120),     # x0.1 V
    ("Volt_5V_sup",    2, 50),      # x0.1 V
    ("Volt_3P3V_sup",  2, 33),      # x0.1 V
]

TX_REGS = SUPPLY_REGS + [
    ("LO_Stat",        2, 0x0001),
    ("Frequency",      2, 9000),
    ("LO_Power",       2, 50),      # x0.1
    ("Sync_stat",      3, 0x000003),  # bit0 sync, bit1 trigger
    ("Tx_loss",        2, 10),      # x0.1
    ("WorkingMode",    1, 1),
    ("Ctrl_bus_err",   2, 0),       # not decoded
    ("Mon_bus_err",    2, 0),       # not decoded
]

RX_REGS = SUPPLY_REGS + [
    ("Sync_stat",      3, 0x000003),  # bit0 sync, bit1 trigger
    ("Rx_loss",        2, 10),      # x0.1
    ("WorkingMode",    1, 1),
    ("Ctrl_bus_err",   2, 0),       # not decoded
    ("Mon_bus_err",    2, 0),       # not decoded
]

RFMON_REGS = [
    VERSION,
    ("Temp_SOM",       2, 48),        # degC
    ("Humidity",       2, 40),        # %
    ("Sync_stat",      3, 0x000003),  # bit0 sync, bit1 trigger
    ("Tx_Power",       2, 6000),     # x0.1 W: 1000 W. rdd RfMonModel.txPower, warns below 700 W
    ("Ref_Power",      2, 100),       # x0.1 W
    ("inputPA_power",  2, 50),        # x0.1
    ("Ctrl_bus_stat",  3, 0x000000),  # rdd shows any non-zero low byte as a bus error
    ("STC_Mode",       1, 1),
    ("inputPA_loss",   2, 20),        # x0.1, rmm decodes the low byte only
    ("irqPA_stat",     2, 0),         # 0 = transmission normal
    ("Ctrl_bus_err",   2, 0),         # not decoded
    ("Mon_bus_err",    2, 0),         # not decoded
    ("FreqF1",         2, 9000),
    ("FreqF2",         2, 9100),
]

DSP_REGS = [
    VERSION,
    ("Temp_SOM",            2, 52),        # degC
    ("Clk_Stat",            1, 0x00),      # bit0 PLL clock error, bit1 RF chip error
    ("Frequency",           2, 0),         # not decoded
    ("TX_powerLevel",       1, 100),
    ("STC_Mode",            1, 1),
    ("WorkingMode",         1, 1),
    ("TempRF",              2, 50),        # degC, rmm decodes the low byte only
    ("HorizontalEncoder",   2, 0),         # int16
    ("VerticalEncoder",     2, 0),         # int16
    ("BusEncoder_stat",     2, 0x0001),    # bit0 encoder bus
    ("Ctrl_bus0_stat",      3, 0x000003),  # bit0 control bus, bit1 monitoring bus
    ("Ctrl_bus1_stat",      3, 0x000003),
    ("Ctrl_bus2_stat",      3, 0x000003),
    ("Ctrl_bus3_stat",      3, 0x000003),
    ("Dual_freq",           1, 0),
    ("Reversed16",          1, 0),         # not decoded
    ("Reversed17",          4, 0),         # not decoded
    ("Reversed18",          4, 0),         # not decoded
    ("FrequencyF1",         1, 0),
    ("WriteFlashMemory",    1, 0),
    ("Reversed21",          1, 0),
    ("Reversed22",          1, 0),
    ("AzimuthCalibAngle",   2, 0),         # uint16
    ("ElevationCalibAngle", 2, 0),         # uint16
]

SPC_REGS = [
    VERSION,
    ("RAW_stat",           1, 0x03),  # bit0 RAW channel, bit1 SEL channel
    ("CFAR_thresh",        2, 10),
    ("PCIe_speed",         2, 10),
    ("cmap_cycle",         1, 32),
    ("GPU_usage",          2, 50),    # %
    ("CPU_usage",          2, 50),    # %
    ("Impulse_Noise",      1, 1),
    ("Replay_RDE",         1, 0),     # not decoded
    ("Reserved9",          1, 0),     # not decoded
    ("SIM_target",         1, 0),
    ("SIM_azimuth",        2, 0),     # x0.1 deg
    ("SIM_elevation",      2, 30),    # x0.1 deg
    ("Reserved13",         2, 0),     # not decoded
    ("Reserved14",         1, 0),     # not decoded
    ("SIM_range",          2, 100),   # x0.1
    ("RawVideoThreshold",  1, 10),
    ("RawVideoPowerLimit", 1, 10),
    ("RawVideoBrightness", 1, 10),
]

# (name, id, reporter, registers), as in device/configuration.xml
DEVICES = [
    ("TX",    0x01, "DSP", TX_REGS),
    ("RX",    0x02, "DSP", RX_REGS),
    ("SYNC",  0x03, "DSP", SYNC_REGS),
    ("RFMON", 0x04, "DSP", RFMON_REGS),
    ("DSP",   0x06, "DSP", DSP_REGS),
    ("SPC",   0x34, "SPC", SPC_REGS),
]

# ---------------------------------------------------------------------------
# --random: how each register is drawn, by name, for every device that has it.
# A register in none of the tables below keeps its table value: Version, the "not decoded"
# registers, and the DSP calibration angles (rdd does not show them).
# ---------------------------------------------------------------------------
# Status bits and on/off controls: register -> bit mask. Every bit in the mask is drawn set or
# clear on its own, so each flag flips between its two states from draw to draw.
FLAGS = {
    "Sync_stat":       0x03,  # TX, RX, RFMON: bit0 sync, bit1 trigger
    "LO_Stat":         0x01,  # TX: 1 locked
    "Ctrl_bus_stat":   0x01,  # RFMON: rdd shows any non-zero low byte as a bus error
    "irqPA_stat":      0x01,  # RFMON: 0 = transmission normal
    "STC_Mode":        0x01,  # RFMON, DSP: STC on/off
    "Clk_Stat":        0x03,  # DSP: bit0 PLL clock, bit1 RF chip
    "BusEncoder_stat": 0x01,  # DSP: bit0 encoder bus
    "Ctrl_bus0_stat":  0x03,  # DSP: bit0 control bus, bit1 monitoring bus
    "Ctrl_bus1_stat":  0x03,
    "Ctrl_bus2_stat":  0x03,
    "Ctrl_bus3_stat":  0x03,
    "Dual_freq":       0x01,  # DSP: dual frequency on/off
    "WriteFlashMemory": 0x01,
    "RAW_stat":        0x03,  # SPC: bit0 RAW channel, bit1 SEL channel
    "Impulse_Noise":   0x01,  # SPC: impulse noise filter on/off
    "SIM_target":      0x01,  # SPC: simulated target on/off
}
# Selectors: register -> the raw values rdd offers for it.
CHOICES = {
    "WorkingMode":   range(9),                 # TX, RX, DSP: rdd combo CĐ0..CĐ8
    "TX_powerLevel": (0, 25, 50, 100),         # DSP: rdd power level buttons, %
    "FrequencyF1":   range(6),                 # DSP: rdd combo index, 9390 + 10 x index MHz
    "Frequency":     range(9390, 9441, 10),    # TX: MHz
    "FreqF1":        range(9390, 9441, 10),    # RFMON: MHz
    "FreqF2":        range(9390, 9441, 10),    # RFMON: MHz
}
# Measurements and numeric controls: register -> (low, high) raw range.
RANGES = {
    "Temp_Board": (35, 65), "Temp_SOM": (35, 65), "TempRF": (40, 70),   # degC
    "Humidity": (30, 70),                                               # %
    "Curr_12V_sup": (10, 20), "Curr_5V_sup": (15, 25), "Curr_3P3V_sup": (5, 12),       # x0.1 A
    "Volt_12V_sup": (114, 126), "Volt_5V_sup": (47, 53), "Volt_3P3V_sup": (31, 35),   # x0.1 V
    "CurrPSU_24Vin": (70, 100), "CurrPSU_12Vout": (50, 70),                           # x0.1 A
    "CurrPSU_5Vout": (30, 50), "CurrPSU_3P3Vout": (20, 30),                           # x0.1 A
    "VoltPSU_12Vout": (114, 126), "VoltPSU_5Vout": (47, 53), "VoltPSU_3P3Vout": (31, 35),  # x0.1 V
    "LO_Power": (40, 60), "Tx_loss": (5, 20), "Rx_loss": (5, 20),       # x0.1
    "Ref_Power": (0, 500),          # x0.1 W: 0-50 W
    "inputPA_power": (30, 70),      # x0.1
    "inputPA_loss": (10, 30),       # x0.1, low byte only
    "GPU_usage": (10, 90), "CPU_usage": (10, 90),                       # %
    "PCIe_speed": (5, 15),          # raw, rdd shows it with 2 decimals
    "CFAR_thresh": (0, 10),         # rdd spin box 0..10
    "cmap_cycle": (0, 32),          # rdd spin box 0..32
    "RawVideoThreshold": (0, 10),   # rdd spin box 0..10
    "RawVideoPowerLimit": (0, 10),  # rdd spin box 0..10
    "RawVideoBrightness": (0, 10),
    "SIM_azimuth": (0, 10),         # rdd shows the raw value as degrees
    "SIM_elevation": (0, 7),        # rdd shows the raw value as degrees
    "SIM_range": (1, 20),           # raw
}
# PAR sector: encoder register -> (low, high) degrees, sent as int16 raw = deg * 32768 / 180.
ENCODERS = {"HorizontalEncoder": (-10.0, 10.0), "VerticalEncoder": (-1.0, 7.0)}
# RFMON Tx_Power, x0.1 W. rdd warns when 0 < P < 700 W (PAR rdd
# app/hmi/pages/mainpage/AlarmLimits.qml). Draws take the low range for TX_POWER_LOW_S seconds,
# then the high range for TX_POWER_HIGH_S seconds, and repeat. Each phase gets at least one
# draw. rdd keeps showing a high value ~10 s after the high phase ends (20-frame max-hold).
TX_POWER_LOW = (4000, 6990)     # 400-699 W: warning
TX_POWER_HIGH = (7000, 10000)   # 700-1000 W: no warning (700 W itself is not an alarm)
TX_POWER_LOW_S = 40
TX_POWER_HIGH_S = 5

def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def build_frame(src, dst, data):
    frame = bytearray([SOP, src, dst]) + (len(data) + 7).to_bytes(2, "little") + data
    frame.append(reduce(xor, frame, 0))
    frame.append(EOP)
    return bytes(frame)


def take_frames(buf):
    """Remove and return every complete, valid frame at the front of buf."""
    frames = []
    while True:
        start = buf.find(SOP)
        if start < 0:
            buf.clear()
            return frames
        del buf[:start]
        if len(buf) < 7:
            return frames
        length = buf[3] | (buf[4] << 8)
        if length < 7 or length > 4096:
            del buf[0]
            continue
        if len(buf) < length:
            return frames
        frame = bytes(buf[:length])
        if frame[-1] != EOP or reduce(xor, frame[:-2], 0) != frame[-2]:
            del buf[0]
            continue
        del buf[:length]
        frames.append(frame)


class Device:
    def __init__(self, name, dev_id, reporter, regs):
        self.name = name
        self.id = dev_id
        self.reporter = reporter
        self.names = [r[0] for r in regs]
        self.sizes = [r[1] for r in regs]
        self.values = [r[2] for r in regs]


class ParSimulator:
    def __init__(self, randomize):
        self.devices = [Device(*d) for d in DEVICES]
        self.by_id = {d.id: d for d in self.devices}
        self.randomize = randomize
        self.lock = threading.Lock()
        self.power_high = False  # Tx_Power phase: low (warning) or high
        self.power_since = None  # monotonic time the current Tx_Power phase started
        self.check_rules()

    def check_rules(self):
        """Stop on a rule naming no register, or a range that does not fit the register."""
        sizes = {}
        for dev in self.devices:
            for name, size in zip(dev.names, dev.sizes):
                sizes.setdefault(name, set()).add(size)
        ranges = dict(RANGES, Tx_Power=(TX_POWER_LOW[0], TX_POWER_HIGH[1]))
        ranges.update({name: (min(values), max(values)) for name, values in CHOICES.items()})
        ranges.update({name: (0, mask) for name, mask in FLAGS.items()})
        for name in ENCODERS:
            ranges[name] = (-32768, 32767)
        for name, (low, high) in ranges.items():
            if name not in sizes:
                raise SystemExit(f"random rule for {name}: no device has that register")
            for size in sizes[name]:
                bits = 8 * size
                if not (-(1 << (bits - 1)) <= low and high < (1 << bits)):
                    raise SystemExit(f"random rule for {name}: {low}..{high} does not fit {bits} bits")

    def random_value(self, name):
        """A new raw value for one register, or None to keep the table value."""
        if name in FLAGS:
            return random.getrandbits(8) & FLAGS[name]
        if name in CHOICES:
            return random.choice(CHOICES[name])
        if name in RANGES:
            return random.randint(*RANGES[name])
        if name in ENCODERS:
            deg = random.uniform(*ENCODERS[name])
            # Inverse of rdd's map: deg = raw * 180 / 32768
            return max(-32768, min(32767, round(deg * 32768.0 / 180.0)))
        if name == "Tx_Power":
            return random.randint(*(TX_POWER_HIGH if self.power_high else TX_POWER_LOW))
        return None

    def draw(self):
        """--random: replace every register that has a rule with a new random value."""
        with self.lock:
            now = time.monotonic()
            if self.power_since is None:
                self.power_since = now
            elif now - self.power_since >= (TX_POWER_HIGH_S if self.power_high else TX_POWER_LOW_S):
                self.power_high = not self.power_high
                self.power_since = now
            for dev in self.devices:
                for i, name in enumerate(dev.names):
                    v = self.random_value(name)
                    if v is not None:
                        dev.values[i] = v
            rfmon = next(d for d in self.devices if d.name == "RFMON")
            power = rfmon.values[rfmon.names.index("Tx_Power")] / 10
        log(f"random draw: RFMON Tx_Power {power:g} W ({'warning' if power < 700 else 'no warning'})")
        self.dump()

    def dump(self):
        """Log every register of every device."""
        with self.lock:
            lines = [f"{d.name}: " + " ".join(f"{n}={v}" for n, v in zip(d.names, d.values)) for d in self.devices]
        for line in lines:
            log(f"  {line}")

    def status_frame(self, dev):
        data = bytearray([0x00])  # first register
        with self.lock:
            for size, v in zip(dev.sizes, dev.values):
                data.append(size - 1)
                data += v.to_bytes(size, "little", signed=v < 0)
        return build_frame(dev.id, RMM_ID, bytes(data))

    def handle_request(self, frame, peer):
        dst = frame[2]
        data = frame[5:-2]
        dev = self.by_id.get(dst)
        dev_name = dev.name if dev else f"id {dst}"
        if len(data) >= 3 and data[0] == CMD_READ:
            return
        if len(data) < 3 or data[0] != CMD_WRITE:
            log(f"<- {peer} unknown request: {frame.hex()}")
            return
        reg, value = data[1], data[2:]
        if dev is None or reg >= len(dev.sizes):
            log(f"<- {peer} write to unknown {dev_name} register {reg}: {frame.hex()}")
            return
        name, size = dev.names[reg], dev.sizes[reg]
        if len(value) != size:
            log(f"<- {peer} write {dev_name}.{name}: {len(value)} bytes but the register holds {size}; ignored")
            return
        new = int.from_bytes(value, "little")
        if not self.randomize:
            log(f"<- {peer} write {dev_name}.{name} (reg {reg}) = {new}: ignored, still sending {dev.values[reg]}")
            return
        with self.lock:
            old = dev.values[reg]
            dev.values[reg] = new
        log(f"<- {peer} write {dev_name}.{name} (reg {reg}): {old} -> {new}, kept until the next random draw")


def client_loop(sim, conn, addr):
    peer = f"{addr[0]}:{addr[1]}"
    log(f"rmm connected from {peer}: sending {', '.join(d.name for d in sim.devices)} every {INTERVAL:g} s")
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.settimeout(0.1)
    buf = bytearray()
    next_send = time.monotonic()
    cycles = 0
    try:
        while True:
            now = time.monotonic()
            if now >= next_send:
                # One send per frame: rmm's DatagramAdapter does not reassemble a frame split across reads.
                for dev in sim.devices:
                    conn.sendall(sim.status_frame(dev))
                cycles += 1
                next_send += INTERVAL
                if next_send < now:
                    next_send = now + INTERVAL
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            for frame in take_frames(buf):
                sim.handle_request(frame, peer)
    except OSError as e:
        log(f"{peer}: {e}")
    finally:
        conn.close()
        log(f"rmm disconnected: {peer} after {cycles} status cycles")


def accept_loop(sim, server):
    while True:
        conn, addr = server.accept()
        threading.Thread(target=client_loop, args=(sim, conn, addr), daemon=True).start()


def open_listener(host, port, devices):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(8)
    log(f"Listening on {host}:{port} for {', '.join(d.name for d in devices)}")
    return server


def main():
    ap = argparse.ArgumentParser(description="PSR PAR status devices (DSP and SPC devices)")
    ap.add_argument("--random", type=float, default=0, metavar="N",
                    help="draw new random values for every register every N seconds, see FLAGS, CHOICES "
                         "and RANGES in this file (default: fixed table values, rmm writes ignored)")
    args = ap.parse_args()
    if args.random < 0:
        ap.error("--random must be positive")

    sim = ParSimulator(args.random > 0)
    if args.random:
        log(f"random mode: new values every {args.random:g} s, rmm writes kept until the next draw")
        sim.draw()
    else:
        log("default mode: fixed table values, rmm writes ignored")
        sim.dump()
    server = open_listener(HOST, PORT, sim.devices)
    threading.Thread(target=accept_loop, args=(sim, server), daemon=True).start()
    try:
        while True:
            if not args.random:
                time.sleep(3600)
                continue
            time.sleep(args.random)
            sim.draw()
    except KeyboardInterrupt:
        log("Stopped")


if __name__ == "__main__":
    main()
