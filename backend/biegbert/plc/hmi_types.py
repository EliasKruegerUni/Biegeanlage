"""Spiegel der SPS-Typen aus 01_DUT (Stand 05.11.2026).

Die Zahlenwerte müssen exakt den Enums im TwinCAT-Projekt entsprechen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class E_Axis(IntEnum):
    None_ = 0
    Z1 = 1
    Z2 = 2
    X = 3
    A = 4
    W = 5


class E_AxisState(IntEnum):
    Disabled = 0
    Standstill = 1
    Moving = 2
    Coupled = 3
    Error = 4


class E_ClampState(IntEnum):
    Unknown = 0
    Open = 1
    Closing = 2
    Clamped = 3
    Opening = 4
    Fault = 5


class E_CmdResult(IntEnum):
    None_ = 0
    Accepted = 1
    RejectedUnknown = 2
    RejectedMode = 3
    RejectedInterlock = 4
    RejectedParam = 5
    RejectedSequence = 6
    RejectedBusy = 7


class E_HmiCmd(IntEnum):
    None_ = 0
    AlarmAck = 10
    ParamApply = 11
    AxisEnable = 100
    AxisDisable = 101
    AxisReset = 102
    AxisHalt = 103
    AxisMoveAbs = 104
    ClampOpen = 120
    ClampClose = 121
    ModePause = 200
    ModeResume1 = 201
    ModeResume2 = 202
    ModeSingleAxis1 = 203
    ModeSingleAxis2 = 204
    ModeReturnToBase = 205
    SimSetInput = 300
    SimInjectFault = 301
    SeqStart = 400
    SeqConfirmQ1 = 401
    SeqConfirmQ2 = 402
    SeqPartRemoved = 403
    PlanLoad = 410
    MaterialConfirm = 411
    CalibrateAxis = 500


class E_KinState(IntEnum):
    Idle = 0
    Loaded = 1
    Coupling = 2
    Running = 3
    Holding = 4
    Held = 5
    Decoupling = 6
    Fault = 7


class E_MotionMode(IntEnum):
    Single = 1
    Coupled = 2
    Hold = 3


class E_OperatingMode(IntEnum):
    Automatic = 10
    Pause = 11
    SingleAxis = 12
    ReturnToBase = 13


class E_AlarmSeverity(IntEnum):
    None_ = 0
    Info = 1
    Warning = 2
    Stop = 3
    Fault = 4


class E_AlarmCode(IntEnum):
    None_ = 0
    HmiHeartbeatLost = 1001
    SimulationActive = 1002
    EStopActive = 1100
    SafetyNotReady = 1101
    AxisError = 2000
    AxisLagError = 2001
    AxisSoftLimit = 2002
    AxisTorqueLimit = 2003
    ClampTimeout = 3000
    ClampPressureLow = 3001
    ClampZoneViolation = 3002
    DistanceZ1Z2 = 4000
    KinCoupleFailed = 5000
    KinMasterFault = 5001
    KinSlaveFault = 5002
    KinPositionDeviation = 5003


def enum_name(enum_cls, value) -> str:
    try:
        return enum_cls(value).name.rstrip("_")
    except ValueError:
        return f"?{value}"


# ── Strukturen (Feldnamen wie in der SPS) ─────────────────────────────

@dataclass
class ST_Material:
    fDiameter_mm: float = 0.0
    fWall_mm: float = 0.0
    fYield_MPa: float = 0.0
    fForceLimit_N: float = 0.0


@dataclass
class ST_HmiParam:
    nPlanId: int = 0
    stMaterial: ST_Material = field(default_factory=ST_Material)
    fFeedOverride_pct: float = 100.0
    fSingleAxisVelo_pct: float = 100.0


@dataclass
class ST_HmiJog:
    eAxis: int = 0
    nDirection: int = 0
    fVelocityFactor_pct: float = 0.0


@dataclass
class ST_HmiCmd:
    nHeartbeat: int = 0
    nCmdId: int = 0
    eCmd: int = 0
    eAxis: int = 0
    nClamp: int = 0
    fPosition: float = 0.0
    fVelocity: float = 0.0
    nValue: int = 0
    stJog: ST_HmiJog = field(default_factory=ST_HmiJog)


@dataclass
class ST_HmiCmdAck:
    nAckId: int = 0
    eResult: int = 0
    nDetail: int = 0


@dataclass
class ST_HmiAxisStatus:
    eState: int = 0
    bEnabled: bool = False
    bReady: bool = False
    bError: bool = False
    nErrorId: int = 0
    fActPos: float = 0.0
    fSetPos: float = 0.0
    fActVelo: float = 0.0
    fSetVelo: float = 0.0
    fLagError: float = 0.0
    fTorque_Nm: float = 0.0


@dataclass
class ST_HmiClampStatus:
    eState: int = 0
    bValve: bool = False
    bOpen: bool = False
    fLastSwitchTime_ms: float = 0.0


@dataclass
class ST_HmiIoStatus:
    bPressureOk: bool = False
    abPanelIn: list = field(default_factory=lambda: [False] * 16)
    abPanelOut: list = field(default_factory=lambda: [False] * 16)


@dataclass
class ST_KinStatus:
    eState: int = 0
    eMode: int = 1
    nPlanId: int = 0
    bError: bool = False
    nErrorId: int = 0
    fMasterPos_mm: float = 0.0


@dataclass
class ST_HmiStatus:
    nLifeCounter: int = 0
    bHmiAlive: bool = False
    bSimulation: bool = False
    bEStopActive: bool = False
    bSafetyOk: bool = False
    eMode: int = E_OperatingMode.Pause
    nStepG2: int = 0
    nStepG3: int = 0
    nBend: int = 0
    dwBaseStateMissing: int = 0
    stCmdAck: ST_HmiCmdAck = field(default_factory=ST_HmiCmdAck)
    aAxis: list = field(default_factory=lambda: [ST_HmiAxisStatus() for _ in range(5)])   # Index 0 = E_Axis.Z1
    aClamp: list = field(default_factory=lambda: [ST_HmiClampStatus() for _ in range(6)])
    stIo: ST_HmiIoStatus = field(default_factory=ST_HmiIoStatus)
    stKin: ST_KinStatus = field(default_factory=ST_KinStatus)
    stParamActive: ST_HmiParam = field(default_factory=ST_HmiParam)


@dataclass
class ST_AlarmEntry:
    eCode: int = 0
    eSeverity: int = 0
    nSource: int = 0
    nDetail: int = 0
    nTimestamp: int = 0      # Windows-Dateizeit, 100 ns
    bActive: bool = False
    bAcked: bool = False


@dataclass
class ST_HmiAlarms:
    nChangeCounter: int = 0
    nCount: int = 0
    bOverflow: bool = False
    eHighestSeverity: int = 0
    aEntry: list = field(default_factory=lambda: [ST_AlarmEntry() for _ in range(32)])


BASE_STATE_BITS = [
    "Z1 nicht in Null-Position", "Z2 nicht in Null-Position", "X nicht in Null-Position",
    "A nicht in Null-Position", "W nicht in Null-Position", "Niederhalter nicht alle offen",
    "Fehler aktiv", "Safety nicht bereit", "Kein Programm geladen", "Material nicht bestätigt",
]
