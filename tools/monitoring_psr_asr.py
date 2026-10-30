#!/usr/bin/env python3
"""PSR simulation for ASR via TCP protocol

    this script  --TCP-->  apprmm  --ROS 2-->  asrrdd

ASR rmm opens one TCP client per reporter (src/app/device/configuration.xml):
    DspDevAddress  ->  TX(1) RX(2) SYNC(3) TEXL(4) MRLR(5) TFSL1-4(6-9) DSP(11)
    SpcDevAddress  ->  SPC(52)
and routes every frame by its SRC id, whichever connection it arrives on. This script serves
all eleven devices on one port and sends every device on every connection, so with both
addresses pointing here rmm decodes each device twice per status cycle.

Frame (device/DatagramAdapter.cpp, device/IDevice.cpp):
    SOP 0x0D | SRC | DST | LEN u16 LE (whole frame) | DATA | XOR of all previous bytes | EOP 0x0A
    DATA = first register number, then for each register: [size - 1][value, little endian]

The register sizes below follow each device's decodeSubMessage(). rmm silently drops a
register whose size differs from what the decoder expects. Values are raw: rmm multiplies
them by the <step> in configuration.xml (0.1 where noted "x0.1").

Two working modes:

default     Sends the table values below every second, like tools/monitor_spc_dsp.py.
            Write commands from rmm (a control changed in rdd) are logged and ignored, so the
            control goes back to the table value on the next status frame.

--random N  Draws new values for every register every N seconds (see FLAGS, CHOICES, RANGES,
            PAIRS): status bits and on/off controls flip between their two states, selectors
            take one of the values rdd offers, measurements take a plausible value for an ASR
            system, the antenna angle takes a random azimuth.
            MRLR Tx_Power stays below rdd's 8000 W warning limit for 40 s, then above it for
            5 s, and repeats, whatever N is (a new value inside the phase's range each draw).
            Each TFSL Amp_IC1-5 current drops below rdd's 0.5 A limit on a few draws
            (TFSL_IC_LOW_CHANCE), so an occasional "Dòng Điện IC Thấp" warning appears.
            A write from rmm is applied and reported until the next draw replaces it.

Every register is printed at start-up, and again after each random draw.

ASR rdd shows MRLR Tx_Power as received (no max-hold, unlike PAR), so the power warning shows
for the whole low phase: about 40 s of every 45 s.

Listens on 0.0.0.0:8888 and sends a status frame for every device every 1 s.

Usage:
    python3 tools/monitoring_psr_asr.py                   # fixed values
    python3 tools/monitoring_psr_asr.py --random 3        # new random values every 3 s

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

TX_REGS = [
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
    ("LO1_Stat",       2, 0x0101),  # low byte LO1, high byte LO2
    ("FreqF1",         2, 1250),    # x0.1
    ("FreqF2",         2, 1250),    # x0.1
    ("LO1_Power",      2, 0x3232),  # low byte LO1, high byte LO2, x0.1 each
    ("Output_Power",   2, -50),     # int16, x0.1
    ("Sync_stat",      2, 0x0003),  # bit0 sync, bit1 trigger
]

RX_REGS = TX_REGS[:10] + [
    ("Sync_stat",      2, 0x0003),  # bit0 sync, bit1 trigger
    ("Reserved",       2, 0),
]

SYNC_REGS = [
    VERSION,
    ("Temp_Board",      2, 45),     # degC
    ("Humidity",        2, 40),     # %
    ("Temp_SOM",        2, 52),     # degC
    ("CurrPSU_24Vin",   2, 85),     # x0.1 A
    ("CurrPSU_12Vout",  2, 60),     # x0.1 A
    ("CurrPSU_5Vout",   2, 40),     # x0.1 A
    ("CurrPSU_3P3Vout", 2, 25),     # x0.1 A
    ("VoltPSU_12Vout",  2, 120),    # x0.1 V
    ("VoltPSU_5Vout",   2, 50),     # x0.1 V
    ("VoltPSU_3P3Vout", 2, 33),     # x0.1 V
    ("Curr_12V_sup",    2, 15),     # x0.1 A
    ("Curr_5V_sup",     2, 20),     # x0.1 A
    ("Curr_3P3V_sup",   2, 8),      # x0.1 A
    ("Volt_12V_sup",    2, 120),    # x0.1 V
    ("Volt_5V_sup",     2, 50),     # x0.1 V
    ("Volt_3P3V_sup",   2, 33),     # x0.1 V
    ("EncoderVal",      2, 0),      # int16 azimuth, -32768..32767 = -180..180 deg
    ("Reserved",        2, 0),
]

TEXL_REGS = [
    VERSION,
    ("Temp_SOM",   2, 48),          # degC
    ("Humidity",   2, 40),          # %
    ("Sync_stat",  2, 0x0003),      # bit0 sync, bit1 trigger
    ("P_in",       2, 100),         # x0.1
    ("P_out",      2, 500),         # x0.1
    ("Amp_IC1",    2, 25),          # x0.1 A
    ("Amp_IC2",    2, 25),          # x0.1 A
    ("Vsup_50v",   2, 500),         # x0.1 V
    ("Reserved",   2, 0),
]

MRLR_REGS = [
    VERSION,
    ("Temp_SOM",   2, 48),          # degC
    ("Humidity",   2, 40),          # %
    ("Sync_stat",  2, 0x0003),      # bit0 sync, bit1 trigger
    ("Tx_Power",   2, 10000),       # W
    ("Ref_Power",  2, 5),
    ("STC_Mode",   1, 1),
    ("Noise_src",  1, 1),
    ("Reserved",   2, 0),
]


def tfsl_regs():
    return [
        VERSION,
        ("Temp_SOM",   2, 48),      # degC
        ("Humidity",   2, 40),      # %
        ("Sync_stat",  2, 0x0003),  # bit0 sync, bit1 trigger
        ("P_in",       2, 100),     # x0.1
        ("P_out",      2, 500),     # x0.1
        ("Amp_IC1",    2, 25),      # x0.1 A
        ("Amp_IC2",    2, 25),      # x0.1 A
        ("Amp_IC3",    2, 25),      # x0.1 A
        ("Amp_IC4",    2, 25),      # x0.1 A
        ("Amp_IC5",    2, 25),      # x0.1 A
        ("Freq1",      2, 0x000A),  # low byte Freq2, high byte Freq1
        ("Vdd_value",  2, 500),     # x0.1 V
        ("Vdd1to4",    2, 0),
        ("Vdd5",       2, 0),
    ]


DSP_REGS = [
    VERSION,
    ("Temp_SOM",          2, 52),        # degC
    ("Clk_Stat",          1, 0x00),      # bit0 PLL clock error, bit1 RF chip error
    ("Frequency",         2, 0),
    ("TX_powerLevel",     1, 100),
    ("STC_Mode",          1, 1),
    ("Noise_src",         1, 1),
    ("WorkingMode",       1, 7),
    ("TempRF",            2, 50),        # degC
    ("Ro_Angle",          2, 0),         # int16 azimuth, -32768..32767 = -180..180 deg
    ("BusEnc_stat",       2, 0x0001),    # bit0 encoder bus, bit1 angle error
    ("Ctrl_bus0_stat",    3, 0x000003),  # bit0 control bus, bit1 monitoring bus
    ("Ctrl_bus1_stat",    3, 0x000003),
    ("Ctrl_bus2_stat",    3, 0x000003),
    ("Ctrl_bus3_stat",    3, 0x000003),
    ("Ctrl_bus4_stat",    3, 0x000003),
    ("Ctrl_bus5_stat",    3, 0x000003),
    ("Ctrl_bus6_stat",    3, 0x000003),
    ("Ctrl_bus7_stat",    3, 0x000003),
    ("Ctrl_bus8_stat",    3, 0x000003),
    ("FreqF1",            2, 255),
    ("FreqF2",            2, 1),
    ("SIM_target",        1, 1),
    ("SIM_rotate_angle",  1, 1),
    ("Reserved24",        1, 0),
    ("Reserved25",        1, 0),
    ("SIM_azi",           2, 50),        # x0.1 deg
    ("SIM_azi_width",     2, 50),        # x0.1 deg
]

SPC_REGS = [
    VERSION,
    ("RAW_stat",               1, 0x03),  # bit0 RAW channel, bit1 SEL channel
    ("CFAR_thresh",            2, 10),
    ("PCIe_speed",             2, 10),
    ("cmap_cycle",             1, 32),
    ("GPU_usage",              2, 50),    # %
    ("CPU_usage",              2, 50),    # %
    ("Impulse_Noise",          1, 1),
    ("Replay_RDE",             1, 0),
    ("Reversed9",              1, 0),
    ("RAW_video_threshold",    1, 10),
    ("RAW_video_limit_power",  1, 10),
    ("RAW_video_contrast",     1, 10),
]

# (name, id, reporter, registers), as in device/configuration.xml
DEVICES = [
    ("TX",    0x01, "DSP", TX_REGS),
    ("RX",    0x02, "DSP", RX_REGS),
    ("SYNC",  0x03, "DSP", SYNC_REGS),
    ("TEXL",  0x04, "DSP", TEXL_REGS),
    ("MRLR",  0x05, "DSP", MRLR_REGS),
    ("TFSL1", 0x06, "DSP", tfsl_regs()),
    ("TFSL2", 0x07, "DSP", tfsl_regs()),
    ("TFSL3", 0x08, "DSP", tfsl_regs()),
    ("TFSL4", 0x09, "DSP", tfsl_regs()),
    ("DSP",   0x0B, "DSP", DSP_REGS),
    ("SPC",   0x34, "SPC", SPC_REGS),
]

# ---------------------------------------------------------------------------
# --random: how each register is drawn. A rule keyed "DEVICE.register" applies to that device
# only and wins over a rule keyed by the register name alone, which applies to every device
# that has the register. A register with no rule keeps its table value: Version, the Reserved
# registers, DSP Frequency and SPC Replay_RDE.
# ---------------------------------------------------------------------------
# Status bits and on/off controls: register -> bit mask. Every bit in the mask is drawn set or
# clear on its own, so each flag flips between its two states from draw to draw.
FLAGS = {
    "Sync_stat":        0x0003,  # TX, RX, TEXL, MRLR, TFSL: bit0 sync, bit1 trigger
    "LO1_Stat":         0x0101,  # TX: low byte LO1, high byte LO2, 1 = locked
    "STC_Mode":         0x01,    # MRLR, DSP: STC on/off
    "Noise_src":        0x01,    # MRLR, DSP: noise source on/off
    "Vdd1to4":          0x0001,  # TFSL: rdd shows non-zero as present
    "Vdd5":             0x0001,  # TFSL: rdd shows non-zero as present
    "Clk_Stat":         0x03,    # DSP: bit0 PLL clock, bit1 RF chip
    "BusEnc_stat":      0x0003,  # DSP: bit0 encoder bus, bit1 angle error
    "Ctrl_bus0_stat":   0x03,    # DSP: bit0 control bus, bit1 monitoring bus
    "Ctrl_bus1_stat":   0x03,
    "Ctrl_bus2_stat":   0x03,
    "Ctrl_bus3_stat":   0x03,
    "Ctrl_bus4_stat":   0x03,
    "Ctrl_bus5_stat":   0x03,
    "Ctrl_bus6_stat":   0x03,
    "Ctrl_bus7_stat":   0x03,
    "Ctrl_bus8_stat":   0x03,
    "SIM_target":       0x01,    # DSP: simulated target on/off
    "SIM_rotate_angle": 0x01,    # DSP: simulated target rotates with the antenna on/off
    "RAW_stat":         0x03,    # SPC: bit0 RAW channel, bit1 SEL channel
    "Impulse_Noise":    0x01,    # SPC: impulse noise filter on/off
}
# Selectors: register -> the raw values rdd offers for it.
CHOICES = {
    "TX_powerLevel": (0, 25, 50, 100),  # DSP: rdd power level buttons, %
    "WorkingMode":   range(8),          # DSP: rdd combo CĐ0..CĐ7
    "DSP.FreqF1":    range(11),         # DSP: rdd frequency combo index (11 entries)
    "DSP.FreqF2":    range(11),
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
    "TX.FreqF1": (1240, 1260), "TX.FreqF2": (1240, 1260),   # x0.1, around the table value
    "Output_Power": (-80, -20),     # TX: int16, x0.1
    "P_in": (80, 120),              # TEXL, TFSL: x0.1
    "P_out": (450, 550),            # TEXL, TFSL: x0.1
    "Amp_IC1": (15, 35), "Amp_IC2": (15, 35), "Amp_IC3": (15, 35),      # TEXL, TFSL: x0.1 A
    "Amp_IC4": (15, 35), "Amp_IC5": (15, 35),                           # (TFSL: see below)
    "Vsup_50v": (480, 520),         # TEXL: x0.1 V
    "Vdd_value": (480, 520),        # TFSL: x0.1 V
    "Ref_Power": (0, 20),           # MRLR: W
    "SIM_azi": (0, 3599),           # DSP: x0.1 deg
    "SIM_azi_width": (10, 100),     # DSP: x0.1 deg
    "GPU_usage": (10, 90), "CPU_usage": (10, 90),                       # SPC: %
    "PCIe_speed": (5, 15),          # SPC: raw, rdd shows it with 2 decimals
    "CFAR_thresh": (0, 10),         # SPC: rdd spin box 0..10
    "cmap_cycle": (0, 32),          # SPC
    "RAW_video_threshold": (0, 10),     # SPC: rdd spin box 0..10
    "RAW_video_limit_power": (0, 10),   # SPC: rdd spin box 0..10
    "RAW_video_contrast": (0, 10),      # SPC
}
# Two values in one register: register -> (low, high) raw range drawn for each byte.
PAIRS = {
    "LO1_Power": (40, 60),          # TX: low byte LO1, high byte LO2, x0.1 each
    "Freq1": (0, 10),               # TFSL: low byte Freq2, high byte Freq1
}
# Antenna azimuth: any int16, rmm maps -32768..32767 to -180..180 deg.
ANGLES = {"Ro_Angle", "EncoderVal"}
# MRLR Tx_Power, W. rdd warns when 0 < P < 8000 W (ASR rdd
# app/hmi/rsp/blocks/AlarmLimits.qml). Draws take the low range for TX_POWER_LOW_S seconds,
# then the high range for TX_POWER_HIGH_S seconds, and repeat. Each phase gets at least one draw.
TX_POWER_LOW = (5000, 7999)     # warning
TX_POWER_HIGH = (8000, 12000)   # no warning (8000 W itself is not an alarm)
TX_POWER_LOW_S = 40
TX_POWER_HIGH_S = 5
# TFSL Amp_IC1-5: rdd warns below 0.5 A while Tx_Power > 0. On each draw every one of the 20 TFSL
# IC currents independently takes the low range with this chance: about half of the draws show
# at least one low IC current.
TFSL_IC_LOW_CHANCE = 0.03
TFSL_IC_LOW = (0, 4)            # x0.1 A: 0-0.4 A


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


def rule(table, dev_name, name):
    """The rule for this device's register: "DEVICE.register" first, then "register"."""
    key = f"{dev_name}.{name}"
    return table.get(key, table.get(name))


class AsrSimulator:
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
                sizes.setdefault(f"{dev.name}.{name}", set()).add(size)
        ranges = dict(RANGES, Tx_Power=(TX_POWER_LOW[0], TX_POWER_HIGH[1]))
        ranges.update({name: (min(values), max(values)) for name, values in CHOICES.items()})
        ranges.update({name: (0, mask) for name, mask in FLAGS.items()})
        ranges.update({name: (0, high | (high << 8)) for name, (low, high) in PAIRS.items()})
        for name in ANGLES:
            ranges[name] = (-32768, 32767)
        for name, (low, high) in ranges.items():
            if name not in sizes:
                raise SystemExit(f"random rule for {name}: no device has that register")
            for size in sizes[name]:
                bits = 8 * size
                if not (-(1 << (bits - 1)) <= low and high < (1 << bits)):
                    raise SystemExit(f"random rule for {name}: {low}..{high} does not fit {bits} bits")

    def random_value(self, dev_name, name):
        """A new raw value for one register, or None to keep the table value."""
        if dev_name.startswith("TFSL") and name.startswith("Amp_IC") and random.random() < TFSL_IC_LOW_CHANCE:
            return random.randint(*TFSL_IC_LOW)
        if (mask := rule(FLAGS, dev_name, name)) is not None:
            return random.getrandbits(24) & mask
        if (values := rule(CHOICES, dev_name, name)) is not None:
            return random.choice(values)
        if (bounds := rule(RANGES, dev_name, name)) is not None:
            return random.randint(*bounds)
        if (bounds := rule(PAIRS, dev_name, name)) is not None:
            return random.randint(*bounds) | (random.randint(*bounds) << 8)
        if name in ANGLES:
            return random.randint(-32768, 32767)
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
                    v = self.random_value(dev.name, name)
                    if v is not None:
                        dev.values[i] = v
            mrlr = next(d for d in self.devices if d.name == "MRLR")
            power = mrlr.values[mrlr.names.index("Tx_Power")]
            low_ic = [f"{d.name}.{n}={v / 10:g} A" for d in self.devices if d.name.startswith("TFSL")
                      for n, v in zip(d.names, d.values) if n.startswith("Amp_IC") and v < 5]
        log(f"random draw: MRLR Tx_Power {power} W ({'warning' if power < 8000 else 'no warning'}), "
            f"low IC current: {', '.join(low_ic) or 'none'}")
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
    ap = argparse.ArgumentParser(description="PSR ASR status devices (DSP and SPC devices)")
    ap.add_argument("--random", type=float, default=0, metavar="N",
                    help="draw new random values for every register every N seconds, see FLAGS, CHOICES, "
                         "RANGES and PAIRS in this file (default: fixed table values, rmm writes ignored)")
    args = ap.parse_args()
    if args.random < 0:
        ap.error("--random must be positive")

    sim = AsrSimulator(args.random > 0)
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
