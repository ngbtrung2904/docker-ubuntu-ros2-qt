#!/usr/bin/env python3
"""Fake ASR SSR modules (TMOS, MSYN-I, MSYN-U, ISSR) for ASR rdp.

    this script  --TCP-->  asrrdpp  --ROS 2 SSR topic-->  asrrdd

The SSR modules are TCP clients of rdp's SSR server (SSRServerPort). rdp tells the modules
apart only by the source IP of each connection (SSRTmosAddress, SSRMsynIAddress,
SSRMsynUAddress, SSRIssrAddress), and drops data from any other address without a log
line. So each simulated module binds its own source IP before it connects. On Linux any
127.x.x.x address works on loopback with no setup.

Frame (src/app/data/mds/ssr/SSRModuleBase.cpp), little endian:
    STX 0x0D0D | SRCID | TYPE | LEN u16 (whole frame) | DATA | XOR of all previous bytes | ETX 0x0A0A
    status DATA = header 0xAA55 + the module's status struct
The status structs and scaling follow SSRModule{TMOS,MSYNI,MSYNU,ISSR}.cpp.

Commands rdp sends to a module (operator actions in rdd: SRCID 0x80, TYPE 0x40) are decoded
and printed. MSYN calibration tables are not simulated: rdd does not display them yet.

--scenario replaces a few values to exercise rdd's alarms (see SCENARIOS): normal, warning,
error, absent, idle, negative, boundary. --cycle rotates through all of them.

Usage:
    python3 tools/monitoring_ssr_asr.py                       # rdp on 127.0.0.1:30010
    python3 tools/monitoring_ssr_asr.py --only tmos,issr      # a subset of modules
    python3 tools/monitoring_ssr_asr.py --rdp 192.168.5.1:30010 --tmos-ip 192.168.5.105 ...
    python3 tools/monitoring_ssr_asr.py --scenario warning
    python3 tools/monitoring_ssr_asr.py --cycle 15            # next scenario every 15 s

With the default source addresses, set in rdp's appconfig.ini, [Device] section:
    SSRServerPort=30010
    SSRTmosAddress=127.0.0.105
    SSRMsynIAddress=127.0.0.137
    SSRMsynUAddress=127.0.0.139
    SSRIssrAddress=127.0.0.103
The last octets mirror the field addresses 192.168.5.105/137/139/103.
"""

import argparse
import errno
import random
import socket
import struct
import threading
import time
from functools import reduce
from operator import xor

STX = b"\x0d\x0d"
ETX = b"\x0a\x0a"
HEADER = 0xAA55
TYPE_STATUS = 0x01
TYPE_CONTROL = 0x40
MSYN_CMD_STATUS = 0x01  # MSYN-I/MSYN-U sub-command; 0x02/0x03 carry calibration tables

TMOS_ID, MSYNI_ID, MSYNU_ID, ISSR_ID = 0x01, 0x02, 0x03, 0x04

# ---------------------------------------------------------------------------
# Nominal raw values; rdp applies the scaling noted on each line.
# ---------------------------------------------------------------------------
TMOS = {
    "volts": [120, 250, 300, 330, 330, 500, 500, 4800],  # x0.01 V: +1.2V +2.5V +3.0V_VREF +3.3V_MW1
                                                         #   +3.3V_MW2 +5.0V_VCC +5.0V_MW +48.0V_MW
    "power_sum": 600,     # x0.1 dBm
    "power_omega": 590,   # x0.1 dBm
    "temp_ltc": 425,      # x0.1 degC
    "temp_zynq": 550,     # x0.1 degC
    "status": 0x1F,       # 1 = OK: bit0 oscillator, bit1 sum, bit2 omega, bit3 gate sum, bit4 gate omega
}

MSYNI = {
    "volts": [120, 250, 330, 400, 500, 600, 1000],  # x0.01 V: +1.2V +2.5V +3.3V +4.0V +5.0V +6.0V +10.0V
    "temp_ltc": 410,      # x0.1 degC
    "temp_zynq": 520,     # x0.1 degC
    "sensi_sum": 780,     # rdp divides by -10: -78.0 dBm
    "sensi_diff": 750,    # rdp divides by -10: -75.0 dBm
    "cal_dev": [3, -2, 4, -1],  # x0.1 dB: HS, LS, HD, LD
    "cal_status": 0x00,   # 2 bits each, 0 ok / 1 warning / 2 error: HG sum, LG sum, HG diff, LG diff
    "cal_phase": 0x00,    # 2 bits each: sum, diff
    "device": 0x00,       # 0 = OK: bit0 OSC, bit1 GTSR, bit2 REC sum, bit3 REC diff, bit4 azimuth
}

MSYNU = dict(MSYNI, device=0x00)  # device: bit0 OSC, bit1 GTSR, bit2 REC sum, bit3 REC diff,
                                  #         bit4 trigger, bit5 azimuth

ISSR = {
    "link": 0x03,         # conSTT: bit0 MSYN-I, bit1 MSYN-U
}

# ---------------------------------------------------------------------------
# Test scenarios: raw values that replace the tables above, per module. A list field takes
# {index: value}. Replaced values are sent without jitter so they sit exactly where set.
# rdd limits (ASR rdd app/hmi/rsp/blocks/AlarmLimits.qml):
#   TMOS power SUM/OMEGA   warning 50 <= P < 58 dBm, error 0 < P < 50 dBm  (0 = transmitter off)
#   TMOS voltages          error outside nominal +-5 %                 } only while SUM > 0 and
#   MSYN-I/U voltages      error outside nominal +-5 % (+10.0V: +-15 %) } OMEGA > 0 (SSR emitting)
#   MSYN-I/U sensitivity   warning below -90 dBm
# Where they show: SsrTransmitterPage and ReceiverPage status bar and status list, and the
# Tx / RI / RU boxes of the MainPage "Máy Phát ĐPTC" block. Order: red, orange, grey (MSYN module
# absent, device bit = 1), green. The status
# bar and boxes name the first failing item; the status list has one row per failing item.
# rdp reads TMOS power, every voltage and every temperature as uint16, so those cannot be
# negative; sensitivity and calibration deviation are int16.
# ---------------------------------------------------------------------------
SCENARIOS = {
    # Every value inside its limits: no alarm.
    "normal": {},
    "warning": {
        "tmos":  {"power_sum": 560, "power_omega": 550},  # 56.0 / 55.0 dBm: "Công Suất SUM Thấp" + "... OMEGA Thấp"
        "msyni": {"sensi_sum": 920, "sensi_diff": 930},   # -92.0 / -93.0 dBm: "RI - Độ Nhạy Kênh SUM Thấp" + "... DIFF Thấp"
        "msynu": {"sensi_sum": 950},                      # -95.0 dBm: "RU - Độ Nhạy Kênh SUM Thấp"
    },
    "error": {
        # +48.0V_MW at 44.0 V "Mức Điện áp Dưới Ngưỡng" outranks SUM 45.0 dBm "Công Suất SUM Dưới Ngưỡng"
        # and OMEGA 55.0 dBm "Công Suất OMEGA Thấp"; the status list shows all three.
        "tmos":  {"power_sum": 450, "power_omega": 550, "volts": {7: 4400}},
        # +5.0V at 4.50 V "RI Mức Điện áp Dưới Ngưỡng", outranking the DIFF sensitivity warning
        "msyni": {"volts": {4: 450}, "sensi_diff": 930},
        # +10.0V at 12.00 V (+20 %): over the limit also reads "RU Mức Điện áp Dưới Ngưỡng"
        "msynu": {"volts": {6: 1200}},
    },
    # One module absent per receiver (status bit = 1), nothing else wrong: grey. MainPage RI box
    # "TT GTSR-I không có", RU box "TT Góc P/Vị không có"; receiver page "Trạng thái GTSR-I không có".
    "absent": {
        "msyni": {"device": 0x02},              # D1 GTSR-I
        "msynu": {"device": 0x20},              # D5 azimuth
    },
    # SSR idle (both powers 0) with the same bad voltages as "error": no voltage alarm anywhere,
    # voltage boxes stay green.
    "idle": {
        "tmos":  {"power_sum": 0, "power_omega": 0, "volts": {7: 4400}},
        "msyni": {"volts": {4: 450}},
        "msynu": {"volts": {6: 1200}},
    },
    "negative": {
        "tmos":  {"power_sum": 0, "power_omega": 0},             # transmitter off: no power alarm
        "msyni": {"sensi_sum": -300, "sensi_diff": -50},         # +30.0 / +5.0 dBm: no warning
        "msynu": {"sensi_sum": -900, "cal_dev": [-250, -120, -300, -5]},  # +90.0 dBm, -25.0 .. -0.5 dB
    },
    # Exactly on the limits, which are not alarms, next to values just past them. Voltages are
    # left out: float rounding makes a value exactly on +-5 % land on either side.
    "boundary": {
        "tmos":  {"power_sum": 580, "power_omega": 500},  # 58.0 no alarm, 50.0 "Công Suất OMEGA Thấp"
        "msyni": {"sensi_sum": 900, "sensi_diff": 901},   # -90.0 no warning, -90.1 "RI - Độ Nhạy Kênh DIFF Thấp"
        "msynu": {"sensi_sum": 900, "sensi_diff": 900},   # -90.0 both: no warning
    },
}

CAL_NAMES = {0x01: "SUM", 0x02: "DIFF", 0x04: "FULL", 0x08: "verify existing", 0x10: "verify new"}
DAC_NAMES = {0: "OMEGA", 1: "DIFF", 2: "SUM"}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def jitter(value, step, static):
    # 0 stays 0: it means "off" (TMOS power), and jitter would turn it into an alarm.
    if static or not step or value == 0:
        return value
    return value + random.randint(-step, step)


def ujitter(value, step, static):
    """jitter for a field rdp reads as unsigned. Only the jitter is clamped: a negative value
    set by hand still reaches struct.pack, so check_scenarios() rejects it."""
    return value if static else max(0, jitter(value, step, static))


def tmos_payload(t, exact, static):
    def j(key, step):
        return ujitter(t[key], step, static or key in exact)
    volts = [ujitter(v, max(1, v // 200), static or "volts" in exact) for v in t["volts"]]
    return struct.pack("<H12HB3x", HEADER, *volts,
                       j("power_sum", 3), j("power_omega", 3), j("temp_ltc", 5), j("temp_zynq", 5),
                       t["status"])


def msyn_payload(m, exact, static):
    volts = [ujitter(v, max(1, v // 200), static or "volts" in exact) for v in m["volts"]]
    return struct.pack("<HB9H6h3B10x", HEADER, MSYN_CMD_STATUS, *volts,
                       ujitter(m["temp_ltc"], 5, static or "temp_ltc" in exact),
                       ujitter(m["temp_zynq"], 5, static or "temp_zynq" in exact),
                       jitter(m["sensi_sum"], 3, static or "sensi_sum" in exact),
                       jitter(m["sensi_diff"], 3, static or "sensi_diff" in exact),
                       *m["cal_dev"], m["cal_status"], m["cal_phase"], m["device"])


def issr_payload(i, exact, static):
    return struct.pack("<HB8x", HEADER, i["link"])


def build_frame(module_id, data_type, payload):
    frame = bytearray(STX) + bytes([module_id, data_type]) + (len(payload) + 9).to_bytes(2, "little") + payload
    frame.append(reduce(xor, frame, 0))
    return bytes(frame + ETX)


def take_frames(buf):
    """Remove and return every complete, valid frame at the front of buf."""
    frames = []
    while True:
        start = buf.find(STX)
        if start < 0:
            del buf[:max(0, len(buf) - 1)]
            return frames
        del buf[:start]
        if len(buf) < 9:
            return frames
        length = buf[4] | (buf[5] << 8)
        if length < 9 or length > 8192:
            del buf[0]
            continue
        if len(buf) < length:
            return frames
        frame = bytes(buf[:length])
        if frame[-2:] != ETX or reduce(xor, frame[:-3], 0) != frame[-3]:
            del buf[0]
            continue
        del buf[:length]
        frames.append(frame)


def on_off(value, bit):
    return "on" if value & (1 << bit) else "off"


def cal_name(cal):
    names = [n for bit, n in CAL_NAMES.items() if cal & bit]
    return "cal " + ("+".join(names) if names else "none") + f" (0x{cal:02X})"


def describe_command(module_id, frame):
    payload = frame[6:-3]
    if frame[3] != TYPE_CONTROL or len(payload) < 2 or struct.unpack_from("<H", payload)[0] != HEADER:
        return f"unrecognised frame {frame.hex()}"
    body = payload[2:]
    if module_id == MSYNI_ID and len(body) >= 208:
        # msyni_cmd: cmdTX u8, prf u16, opt u8, id[3], pattern[8], sector[16] {beg u16, end u16, pattern[8]}, cal u8
        cmd_tx, prf, opt = struct.unpack_from("<BHB", body)
        parts = [f"RF {on_off(cmd_tx, 0)}", f"stagger {on_off(cmd_tx, 1)}",
                 f"P_sum level {(cmd_tx >> 2) & 7}", f"P_omega level {(cmd_tx >> 5) & 7}", f"PRF {prf}",
                 f"map {on_off(opt, 0)}", f"ISLS {on_off(opt, 1)}", f"IISLS {on_off(opt, 2)}",
                 f"DAC {DAC_NAMES.get((opt >> 3) & 3, (opt >> 3) & 3)}", f"all-call {on_off(opt, 5)}",
                 f"target id {int.from_bytes(body[4:7], 'little'):06X}", f"pattern {body[7:15].hex()}",
                 cal_name(body[207])]
        sectors = []
        for i in range(16):
            beg, end = struct.unpack_from("<HH", body, 15 + 12 * i)
            if beg or end:
                sectors.append(f"{i}:{beg * 360 / 65536:.1f}-{end * 360 / 65536:.1f}deg/{body[19 + 12 * i:27 + 12 * i].hex()}")
        if sectors:
            parts.append("sectors " + " ".join(sectors))
        return ", ".join(parts)
    if module_id == MSYNU_ID and len(body) >= 2:
        return f"DAC {DAC_NAMES.get(body[1] & 3, body[1] & 3)}, {cal_name(body[0])}"
    if module_id == ISSR_ID and len(body) >= 1:
        return f"signal generator {'on' if body[0] == 0x88 else 'off'} (0x{body[0]:02X})"
    return f"unrecognised command {frame.hex()}"


class Module:
    def __init__(self, name, module_id, src_ip, payload_fn, base):
        self.name = name
        self.id = module_id
        self.src_ip = src_ip
        self.payload_fn = payload_fn
        self.base = base
        self.state = (base, set())  # (values, fields sent without jitter)

    def scenario_values(self, overrides):
        values = dict(self.base)
        for key, value in overrides.items():
            if key not in values:
                raise KeyError(key)
            if isinstance(value, dict):
                items = list(values[key])
                for index, item in value.items():
                    items[index] = item
                value = items
            values[key] = value
        return values

    def apply(self, overrides):
        # One reference swap, so the module's thread never sees values and exact out of step.
        self.state = (self.scenario_values(overrides), set(overrides))

    def status_frame(self, static):
        values, exact = self.state
        return build_frame(self.id, TYPE_STATUS, self.payload_fn(values, exact, static))


def check_scenarios(modules):
    """Fail at start, not in a module thread, on a value its struct field cannot hold."""
    for scenario, per_module in SCENARIOS.items():
        for key, overrides in per_module.items():
            module = modules.get(key)
            if module is None:
                raise SystemExit(f"scenario {scenario}: unknown module {key}")
            try:
                module.payload_fn(module.scenario_values(overrides), set(overrides), True)
            except (KeyError, IndexError, struct.error) as e:
                raise SystemExit(f"scenario {scenario}: {module.name} {overrides}: {e}")


def apply_scenario(modules, name):
    for key, module in modules.items():
        module.apply(SCENARIOS[name].get(key, {}))
    changes = " ".join(f"{key}.{field}={value}" for key, overrides in SCENARIOS[name].items()
                       for field, value in overrides.items())
    log(f"scenario {name}: {changes or 'table values'}")


def connect(module, rdp):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((module.src_ip, 0))
        sock.settimeout(3.0)
        sock.connect(rdp)
    except OSError:
        sock.close()
        raise
    return sock


def run_module(module, rdp, interval, static, verbose):
    rdp_text = f"{rdp[0]}:{rdp[1]}"
    last_error = None
    while True:
        try:
            sock = connect(module, rdp)
        except OSError as e:
            if e.errno == errno.EADDRNOTAVAIL:
                text = f"source address {module.src_ip} is not on this host (use 127.x.x.x or add it to an interface)"
            else:
                text = f"cannot reach rdp at {rdp_text}: {e.strerror or e}"
            if text != last_error:
                log(f"{module.name}: {text}; retrying every 2 s")
                last_error = text
            time.sleep(2.0)
            continue
        last_error = None
        log(f"{module.name}: connected {module.src_ip} -> {rdp_text}, status every {interval:g} s")
        sock.settimeout(0.1)
        buf = bytearray()
        next_send = time.monotonic()
        frames_sent = 0
        try:
            while True:
                now = time.monotonic()
                if now >= next_send:
                    frame = module.status_frame(static)
                    sock.sendall(frame)
                    frames_sent += 1
                    if verbose:
                        log(f"-> {module.name}: {frame.hex()}")
                    next_send += interval
                    if next_send < now:
                        next_send = now + interval
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("closed by rdp")
                buf += chunk
                for frame in take_frames(buf):
                    log(f"<- {module.name} command: {describe_command(module.id, frame)}")
        except OSError as e:
            log(f"{module.name}: disconnected after {frames_sent} status frames: {e}")
        finally:
            sock.close()
        time.sleep(2.0)


def parse_host_port(text):
    host, sep, port = text.rpartition(":")
    if not sep or not host or not port.isdigit():
        raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {text!r}")
    return host, int(port)


def main():
    ap = argparse.ArgumentParser(description="Fake ASR SSR modules (TMOS, MSYN-I, MSYN-U, ISSR) for ASR rdp.")
    ap.add_argument("--rdp", type=parse_host_port, default=("127.0.0.1", 30010),
                    help="rdp SSR server, HOST:PORT (rdp SSRServerPort; default: 127.0.0.1:30010)")
    ap.add_argument("--tmos-ip", default="127.0.0.105", help="source IP = rdp SSRTmosAddress (default: %(default)s)")
    ap.add_argument("--msyni-ip", default="127.0.0.137", help="source IP = rdp SSRMsynIAddress (default: %(default)s)")
    ap.add_argument("--msynu-ip", default="127.0.0.139", help="source IP = rdp SSRMsynUAddress (default: %(default)s)")
    ap.add_argument("--issr-ip", default="127.0.0.103", help="source IP = rdp SSRIssrAddress (default: %(default)s)")
    ap.add_argument("--only", default="tmos,msyni,msynu,issr",
                    help="comma-separated modules to simulate (default: %(default)s)")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between status frames (default: %(default)s)")
    ap.add_argument("--static", action="store_true", help="send the nominal values unchanged, no jitter")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every frame sent")
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="normal",
                    help="values to send, see SCENARIOS in this file (default: %(default)s)")
    ap.add_argument("--cycle", type=float, default=0,
                    help="move to the next scenario every CYCLE seconds, starting from --scenario")
    args = ap.parse_args()
    if args.interval <= 0:
        ap.error("--interval must be positive")
    if args.cycle < 0:
        ap.error("--cycle must not be negative")

    modules = {
        "tmos": Module("TMOS", TMOS_ID, args.tmos_ip, tmos_payload, TMOS),
        "msyni": Module("MSYN-I", MSYNI_ID, args.msyni_ip, msyn_payload, MSYNI),
        "msynu": Module("MSYN-U", MSYNU_ID, args.msynu_ip, msyn_payload, MSYNU),
        "issr": Module("ISSR", ISSR_ID, args.issr_ip, issr_payload, ISSR),
    }
    check_scenarios(modules)
    apply_scenario(modules, args.scenario)
    selected = [s.strip().lower() for s in args.only.split(",") if s.strip()]
    unknown = [s for s in selected if s not in modules]
    if unknown or not selected:
        ap.error(f"--only: unknown module(s) {', '.join(unknown)}; choose from {', '.join(modules)}")

    for key in selected:
        threading.Thread(target=run_module, daemon=True,
                         args=(modules[key], args.rdp, args.interval, args.static, args.verbose)).start()
    names = list(SCENARIOS)
    scenario = args.scenario
    try:
        while True:
            if not args.cycle:
                time.sleep(3600)
                continue
            time.sleep(args.cycle)
            scenario = names[(names.index(scenario) + 1) % len(names)]
            apply_scenario(modules, scenario)
    except KeyboardInterrupt:
        log("Stopped")


if __name__ == "__main__":
    main()
