"""Klartexte für Alarme, Kommandoergebnisse und Ablaufschritte (die SPS liefert nur Codes)."""
from __future__ import annotations

from .plc.hmi_types import BASE_STATE_BITS, E_AlarmCode as AC, E_Axis, E_CmdResult as R

ALARM_TEXT = {
    AC.HmiHeartbeatLost: "Heartbeat der Bedienung ausgefallen – Bewegungen gestoppt, Automatik in Pause",
    AC.SimulationActive: "Simulationsbetrieb aktiv",
    AC.EStopActive: "Not-Halt ausgelöst",
    AC.SafetyNotReady: "TwinSAFE nicht betriebsbereit",
    AC.AxisError: "Achsfehler",
    AC.AxisLagError: "Schleppfehler / Geschwindigkeitsüberschreitung",
    AC.AxisSoftLimit: "Software-Endlage erreicht",
    AC.AxisTorqueLimit: "Momentgrenze erreicht",
    AC.ClampTimeout: "Niederhalter: Zeitüberwachung",
    AC.ClampPressureLow: "Niederhalter: Klemmdruck fehlt",
    AC.ClampZoneViolation: "Futter in der Zone eines geschlossenen Niederhalters",
    AC.DistanceZ1Z2: "Abstand Vorschub–Führung unterschritten",
    AC.KinCoupleFailed: "Kopplung fehlgeschlagen",
    AC.KinMasterFault: "Master Z1 gestört",
    AC.KinSlaveFault: "Slave gestört",
    AC.KinPositionDeviation: "Achse weicht vor Wiederanlauf vom Plan ab",
}

RESULT_TEXT = {
    R.None_: "keine Antwort",
    R.Accepted: "angenommen",
    R.RejectedUnknown: "abgelehnt: Kommando unbekannt",
    R.RejectedMode: "abgelehnt: in dieser Betriebsart nicht erlaubt",
    R.RejectedInterlock: "abgelehnt: Verriegelung aktiv",
    R.RejectedParam: "abgelehnt: Parameter ungültig",
    R.RejectedSequence: "abgelehnt: zweite Stufe ohne gültige erste Stufe",
    R.RejectedBusy: "abgelehnt: Gerät beschäftigt",
}

STEP_G2_TEXT = {0: "–", 20: "S20 Startposition anfahren", 21: "S21 Niederhalter schließen",
                23: "S23 Biegen (gekoppelt)", 24: "S24 Ende – Geometrie halten, Bestätigung Q1",
                25: "S25 Lösen und Biegekopf zurück"}
STEP_G3_TEXT = {0: "–", 30: "S30 Halten für Rotation", 31: "S31 W dreht"}


def source_text(code: int, source: int) -> str:
    if source == 0:
        return ""
    if 2000 <= code < 3000 or code in (AC.DistanceZ1Z2,):
        try:
            return E_Axis(source).name
        except ValueError:
            return f"Achse {source}"
    if 3000 <= code < 4000:
        return f"Niederhalter {source}"
    return str(source)


def detail_text(result: int, detail: int) -> str:
    """Erklärt nDetail einer Ablehnung (Konvention der Simulation, in der SPS festzulegen)."""
    if not detail:
        return ""
    if result == R.RejectedInterlock and detail & 0x10000:
        axes = [E_Axis(i).name for i in range(1, 6) if detail & (1 << i)]
        return "Achse nicht freigegeben oder gestört: " + ", ".join(axes)
    if result == R.RejectedInterlock and detail == 0x20000:
        return "Z1 steht hinter dem Start der ersten Biegung"
    try:
        return ALARM_TEXT[AC(detail)]
    except ValueError:
        pass
    if result == R.RejectedInterlock and detail < (1 << 10):
        bits = [BASE_STATE_BITS[i] for i in range(10) if detail & (1 << i)]
        if bits:
            return "; ".join(bits)
    if result == R.RejectedParam:
        return {1: "Zielposition außerhalb der Grenzen bzw. Plan fehlt", 2: "Geschwindigkeit ungültig",
                10: "Plan: z1_start ≥ z1_fin", 11: "Plan: s nicht streng monoton",
                12: "Plan: u oder φ außerhalb der Grenzen", 13: "Plan: Z2 außerhalb der Grenzen",
                14: "Plan: Z1-Ende hinter der Kollisionsgrenze"}.get(detail, f"Detail {detail}")
    return f"Detail {detail}"
