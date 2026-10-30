#!/usr/bin/env python3
"""Siemens S7 PLC emulator for PAR (Snap7 server)

    this script  <--S7 (Snap7)-->  apprmm  --ROS 2-->  apprddp

PAR rmm (src/app/device/gplc/) connects to PlcDevAddress ("ip;rack;slot", port 102) and:
    every 1 s  reads DB14 bytes 0..115   PDevMonitor  (PLC -> PC status)
    on command writes DB13               PDevControl  (PC -> PLC commands, joystick)
A bit command is a read-modify-write of one DB13 byte; the on/off commands set one bit, wait
150 ms, then clear its partner bit. REAL setpoints are 4 bytes, big endian.

Every DB13 write is decoded and printed by name. As on a real PLC the written bits stay in DB13,
but nothing here feeds them back into DB14: the status values come only from the table below
(default) or from the random draw (--random N). Values are raw, as rmm reads them.

Usage (port 102 needs root, so run inside the container or with sudo):
    python3 tools/monitoring_plc_par.py                 # fixed values
    python3 tools/monitoring_plc_par.py --random 3      # new random values every 3 s
    python3 tools/monitoring_plc_par.py --port 1102     # unprivileged port, for a test client

Point rmm at it in its appconfig.ini, [Device] section:
    PlcDevAddress="127.0.0.1;0;1"
"""

from s7server import PlcSimulator, bit, real, run, word

MON_DB, MON_SIZE = 14, 116    # GrpPlcDevice mReadDBNumber, PDevMonitor mByteSize
CTL_DB, CTL_SIZE = 13, 16     # GrpPlcDevice mWriteDBNumber, PDevControl mByteSize

# DB14, PLC -> PC (PDevMonitor::unpackResponse)
MONITOR = [
    bit("signal24v_sup", 0, 0, 1),
    bit("signal27v_sup", 0, 1, 1),
    bit("signalBrake_ELE", 0, 2, 0),
    bit("signalBrake_AZI", 0, 3, 0),
    bit("signalDirecUp", 0, 4, 0),
    bit("signalDirecDown", 0, 5, 0),
    bit("signalDirecLeft", 0, 6, 0),
    bit("signalDirecRight", 0, 7, 0),
    bit("signalInverter4_OK_PAR", 1, 4, 1),
    bit("signalInverter4_Error_PAR", 1, 5, 0),
    bit("signalInverter5_OK_PAR", 1, 6, 1),
    bit("signalInverter5_Error_PAR", 1, 7, 0),
    bit("signalBuzzerOn_PAR", 3, 0, 0),
    bit("signalHMIRequest", 3, 1, 0),
    bit("signalPowerIN_InvertPan", 3, 2, 1),
    bit("signalPowerIn_InvertTilt", 3, 3, 1),
    bit("signalINV4_Rotation", 4, 3, 1),
    bit("signalINV5_Azi", 4, 4, 0),
    bit("signalINV5_elevation", 4, 5, 0),
    bit("signalPAR_running", 4, 6, 1),
    bit("signalTilt_AZI_running", 4, 7, 0),
    bit("signalTilt_ELE_running", 5, 0, 0),
    real("signalReal_SpeedRotation", 6, 10.0),
    real("signalReal_TiltAngle", 10, 0.0),
    word("signalINV4_Freq", 46, 50),
    word("signalINV4_Volt", 48, 380),
    word("signalINV4_Speed", 50, 1450),
    word("signalINV4_Curr", 52, 40),
    word("signalINV4_Power", 54, 1500),
    word("signalINV4_ControlWord", 58, 0x047F),
    word("signalINV4_StatusWord", 60, 0x0237),
    word("signalINV5_AZ_Freq", 66, 0),
    word("signalINV5_AZ_Volt", 68, 0),
    word("signalINV5_AZ_Speed", 70, 0),
    word("signalINV5_AZ_Curr", 72, 0),
    word("signalINV5_AZ_Power", 74, 0),
    word("signalINV5_AZ_ControlWord", 78, 0x047E),
    word("signalINV5_AZ_StatusWord", 80, 0x0231),
    word("signalINV5_ELE_Freq", 86, 0),
    word("signalINV5_ELE_Volt", 88, 0),
    word("signalINV5_ELE_Speed", 90, 0),
    word("signalINV5_ELE_Curr", 92, 0),
    word("signalINV5_ELE_Power", 94, 0),
    word("signalINV5_ELE_ControlWord", 98, 0x047E),
    word("signalINV5_ELE_StatusWord", 100, 0x0231),
    word("signalPAR_Angle1", 106, 0),
    word("signalPAR_Angle2", 108, 0),
    word("signalPAR_Angle3", 110, 0),
    word("signalPAR_Angle4", 112, 0),
]

# DB13, PC -> PLC (PDevControl). "on"/"off" pairs: the command sets one and clears the other.
CONTROL = [
    bit("OnPAR", 0, 0),
    bit("PcControl (on: PC, off: HMI)", 0, 1),
    bit("AntennaRotation on", 0, 4),
    bit("AntennaRotation off", 0, 5),
    bit("JoyStickMotionOn", 0, 6),
    bit("JoyStickTiltAziUp", 0, 7),
    bit("JoyStickTiltAziDown", 1, 0),
    bit("JoyStickTiltEleLeft", 1, 1),
    bit("JoyStickTiltEleRight", 1, 2),
    bit("PowerPAR (reset pulse)", 1, 3),
    bit("HmiControl", 1, 5),
    real("AntennaRotationSpeed", 4),
    real("AntennaTiltAziAngle", 8),
    real("AntennaTiltEleAngle", 12),
]

# --random: ranges for numbers; every bool flips except these, which keep their table value.
RANGES = {
    "signalReal_SpeedRotation": (0.0, 15.0), "signalReal_TiltAngle": (-5.0, 10.0),
    "signalINV4_Freq": (0, 50), "signalINV4_Volt": (0, 400), "signalINV4_Speed": (0, 1500),
    "signalINV4_Curr": (0, 100), "signalINV4_Power": (0, 3000),
    "signalINV5_AZ_Freq": (0, 50), "signalINV5_AZ_Volt": (0, 400), "signalINV5_AZ_Speed": (0, 1500),
    "signalINV5_AZ_Curr": (0, 100), "signalINV5_AZ_Power": (0, 3000),
    "signalINV5_ELE_Freq": (0, 50), "signalINV5_ELE_Volt": (0, 400), "signalINV5_ELE_Speed": (0, 1500),
    "signalINV5_ELE_Curr": (0, 100), "signalINV5_ELE_Power": (0, 3000),
    "signalPAR_Angle1": (-2000, 2000), "signalPAR_Angle2": (-2000, 2000),
    "signalPAR_Angle3": (-2000, 2000), "signalPAR_Angle4": (-2000, 2000),
}
FIXED = {"signal24v_sup", "signal27v_sup"}
PAIRS = [
    ("signalInverter4_OK_PAR", "signalInverter4_Error_PAR"),
    ("signalInverter5_OK_PAR", "signalInverter5_Error_PAR"),
]


def make_sim():
    return PlcSimulator(MON_DB, MON_SIZE, MONITOR, CTL_DB, CTL_SIZE, CONTROL, RANGES, FIXED, PAIRS)


if __name__ == "__main__":
    run("PAR S7 PLC emulator (Snap7 server)", make_sim, __doc__)
