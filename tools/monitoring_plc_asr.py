#!/usr/bin/env python3
"""Siemens S7 PLC emulator for ASR (Snap7 server)

    this script  <--S7 (Snap7)-->  apprmm  --ROS 2-->  asrrdd

ASR rmm (src/app/device/gplc/) connects to PlcDevAddress ("ip;rack;slot", port 102) and:
    every 1 s  reads DB14 bytes 0..115   PDevMonitor  (PLC -> PC status)
    on command writes DB13               PDevControl  (PC -> PLC commands)
A bit command is a read-modify-write of one DB13 byte; most commands set one bit, wait 150 ms,
then clear its partner bit (on/off pairs below). REAL setpoints are 4 bytes, big endian.

Every DB13 write is decoded and printed by name. As on a real PLC the written bits stay in DB13,
but nothing here feeds them back into DB14: the status values come only from the table below
(default) or from the random draw (--random N).

Values are raw, as rdd shows them, except the inverter 1 and 2 currents that rdd divides by 10.

Known rmm quirk, emulated as is: rmm reads outputCurrentTiltInverterASR as a REAL at byte 92 but
then reads tempWindingTiltInverterASR at byte 94, inside that REAL, so the temperature shown is
the REAL's low half. The table does not list byte 94.

Usage (port 102 needs root, so run inside the container or with sudo):
    python3 tools/monitoring_plc_asr.py                 # fixed values
    python3 tools/monitoring_plc_asr.py --random 3      # new random values every 3 s
    python3 tools/monitoring_plc_asr.py --port 1102     # unprivileged port, for a test client

Point rmm at it in its appconfig.ini, [Device] section:
    PlcDevAddress="127.0.0.1;0;1"
"""

from s7server import PlcSimulator, bit, real, run, word

MON_DB, MON_SIZE = 14, 116    # GrpPlcDevice mReadDBNumber, PDevMonitor mByteSize
CTL_DB, CTL_SIZE = 13, 26     # GrpPlcDevice mWriteDBNumber; the last REAL ends at byte 25

# DB14, PLC -> PC (PDevMonitor::unpackResponse)
MONITOR = [
    bit("signalSw10xProtection", 0, 0, 1),
    bit("signalPowerOnASR", 0, 1, 1),
    bit("signalPowerOnPAR", 0, 2, 1),
    bit("signalRotationASR", 0, 3, 1),
    bit("signalCabinTemperatureASR", 0, 4, 0),
    bit("signalCabinMotionSensorASR", 0, 5, 0),
    bit("signalCabinSmokeSensorASR", 0, 6, 0),
    bit("signalOverVoltageASR", 0, 7, 0),
    bit("signalMainASR", 1, 0, 1),
    bit("signalUpsOn", 1, 1, 1),
    bit("signalSp20", 1, 2, 0),
    bit("signalInverter1_OK_ASR", 1, 4, 1),
    bit("signalInverter1_Error_ASR", 1, 5, 0),
    bit("signalInverter2_OK_ASR", 1, 6, 1),
    bit("signalInverter2_Error_ASR", 1, 7, 0),
    bit("signalAUCLocalControlASR", 2, 0, 0),
    bit("signalAUCRemoteControlASR", 2, 1, 1),
    bit("signalAUCTiltDownControlASR", 2, 2, 0),
    bit("signalAUCTiltUpControlASR", 2, 3, 0),
    bit("signalAUCTransportControlASR", 2, 4, 0),
    bit("signalAUCObstructionLightASR", 2, 5, 1),
    bit("signalAUCReady2TransportASR", 2, 6, 0),
    bit("signalAUCEmergencyStopASR", 2, 7, 0),
    bit("signalAUCAlarmBellOnASR", 3, 0, 0),
    bit("signalHmiRequest", 3, 1, 0),
    bit("signalTiltInverterOnASR", 3, 2, 1),
    bit("signalTiltLowerPositionASR", 3, 3, 0),
    bit("signalTiltUpperPositionASR", 3, 5, 0),
    bit("signalTiltParkingPositionASR", 3, 7, 0),
    bit("signalTiltInverter_OK_ASR", 4, 1, 1),
    bit("signalTiltInverter_Error_ASR", 4, 2, 0),
    bit("signalInverter1OutputOnASR", 4, 3, 1),
    bit("signalInverter2OutputOnASR", 4, 4, 1),
    bit("signalTiltInverterOutputOnASR", 4, 5, 0),
    bit("signalMotor1RunningASR", 4, 6, 1),
    bit("signalMotor2RunningASR", 4, 7, 1),
    bit("signalTiltMotorRunningASR", 5, 0, 0),
    real("antenSpeedASR", 6, 15.0),            # rpm
    real("antenTiltAngleASR", 10, 2.5),        # deg
    word("inputVoltageP1", 14, 220),           # V
    word("inputVoltageP2", 16, 221),
    word("inputVoltageP3", 18, 219),
    word("totalConsumptionPower", 20, 4500),   # W
    word("consumptionPowerP1", 22, 1500),
    word("consumptionPowerP2", 24, 1500),
    word("consumptionPowerP3", 26, 1500),
    word("totalConsumptionCurrent", 28, 21),   # A
    word("consumptionCurrentP1", 30, 7),
    word("consumptionCurrentP2", 32, 7),
    word("consumptionCurrentP3", 34, 7),
    word("inputFrequency", 36, 50),            # Hz
    word("activeTime", 38, 1200),
    word("cosPhi", 40, 95),
    word("cabinTemperature", 42, 28),          # degC
    word("cabinHumidity", 44, 55),             # %
    word("outputFreqInverter1ASR", 46, 50),
    word("outputVoltageInverter1ASR", 48, 380),
    word("motorSpeedInverter1ASR", 50, 1450),
    word("outputCurrentInverter1ASR", 52, 52),  # x0.1 A in rdd
    word("outputPower1ASR", 54, 2000),
    word("tempWindingInverter1ASR", 56, 45),
    word("controlWordInverter1ASR", 58, 0x047F),
    word("statusWordInverter1ASR", 60, 0x0237),
    word("outputFreqInverter2ASR", 66, 50),
    word("outputVoltageInverter2ASR", 68, 380),
    word("motorSpeedInverter2ASR", 70, 1450),
    word("outputCurrentInverter2ASR", 72, 52),  # x0.1 A in rdd
    word("outputPower2ASR", 74, 2000),
    word("tempWindingInverter2ASR", 76, 45),
    word("controlWordInverter2ASR", 78, 0x047F),
    word("statusWordInverter2ASR", 80, 0x0237),
    word("outputFreqTiltInverterASR", 86, 0),
    word("outputVoltageTiltInverterASR", 88, 0),
    word("motorSpeedTiltInverterASR", 90, 0),
    real("outputCurrentTiltInverterASR", 92, 0.0),  # bytes 92..95, see the quirk above
    word("controlWordTiltInverterASR", 98, 0x047E),
    word("statusWordTiltInverterASR", 100, 0x0231),
    word("encoderAngle1", 106, 0),
    word("encoderAngle2", 108, 0),
]

# DB13, PC -> PLC (PDevControl). "on"/"off" pairs: the command sets one and clears the other.
CONTROL = [
    bit("PcControl (on: PC, off: HMI)", 0, 1),
    bit("PowerASR on", 0, 2),
    bit("PowerASR off", 0, 3),
    bit("PowerVFD1 on", 0, 4),
    bit("PowerVFD1 off", 0, 5),
    bit("PowerVFD2 on", 0, 6),
    bit("PowerVFD2 off", 0, 7),
    bit("VFDMotor1", 1, 0),
    bit("VFDMotor2", 1, 1),
    bit("VFDDual", 1, 2),
    bit("AsrOn (antenna rotation) on", 1, 3),
    bit("AsrOn (antenna rotation) off", 1, 4),
    bit("HmiControl", 1, 5),
    bit("AA on", 1, 6),
    bit("AA off", 1, 7),
    bit("TiltDown", 2, 0),
    bit("TiltUp", 2, 1),
    bit("TiltCustom", 2, 2),
    bit("Light on", 3, 0),
    bit("Light off", 3, 1),
    bit("PAR on", 3, 2),
    bit("PAR off", 3, 3),
    bit("AngleTiltReset", 3, 4),
    real("RotationSpeed", 6),
    real("TiltAngle", 10),
    real("HighPosAngle", 14),
    real("ZeroPosAngle", 18),
    real("LowPosAngle", 22),
]

# --random: ranges for numbers; every bool flips except these, which keep their table value.
RANGES = {
    "antenSpeedASR": (5.0, 15.0), "antenTiltAngleASR": (-2.0, 5.0),
    "inputVoltageP1": (210, 230), "inputVoltageP2": (210, 230), "inputVoltageP3": (210, 230),
    "totalConsumptionPower": (3000, 6000),
    "consumptionPowerP1": (1000, 2000), "consumptionPowerP2": (1000, 2000), "consumptionPowerP3": (1000, 2000),
    "totalConsumptionCurrent": (15, 27),
    "consumptionCurrentP1": (5, 9), "consumptionCurrentP2": (5, 9), "consumptionCurrentP3": (5, 9),
    "inputFrequency": (49, 51), "activeTime": (0, 30000), "cosPhi": (85, 99),
    "cabinTemperature": (20, 45), "cabinHumidity": (30, 90),
    "outputFreqInverter1ASR": (0, 50), "outputVoltageInverter1ASR": (0, 400),
    "motorSpeedInverter1ASR": (0, 1500), "outputCurrentInverter1ASR": (0, 100),
    "outputPower1ASR": (0, 3000), "tempWindingInverter1ASR": (25, 80),
    "outputFreqInverter2ASR": (0, 50), "outputVoltageInverter2ASR": (0, 400),
    "motorSpeedInverter2ASR": (0, 1500), "outputCurrentInverter2ASR": (0, 100),
    "outputPower2ASR": (0, 3000), "tempWindingInverter2ASR": (25, 80),
    "outputFreqTiltInverterASR": (0, 50), "outputVoltageTiltInverterASR": (0, 400),
    "motorSpeedTiltInverterASR": (0, 1500), "outputCurrentTiltInverterASR": (0.0, 5.0),
    "encoderAngle1": (0, 3599), "encoderAngle2": (0, 3599),
}
FIXED = {"signalAUCEmergencyStopASR", "signalCabinSmokeSensorASR"}
PAIRS = [
    ("signalInverter1_OK_ASR", "signalInverter1_Error_ASR"),
    ("signalInverter2_OK_ASR", "signalInverter2_Error_ASR"),
    ("signalTiltInverter_OK_ASR", "signalTiltInverter_Error_ASR"),
]


def make_sim():
    return PlcSimulator(MON_DB, MON_SIZE, MONITOR, CTL_DB, CTL_SIZE, CONTROL, RANGES, FIXED, PAIRS)


if __name__ == "__main__":
    run("ASR S7 PLC emulator (Snap7 server)", make_sim, __doc__)
