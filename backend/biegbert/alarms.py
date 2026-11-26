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
    AC.KinCoupleFailed: "Kopplung oder Kurvenscheibe fehlgeschlagen",
    AC.KinMasterFault: "Master Z1 gestört",
    AC.KinSlaveFault: "Slave gestört",
    AC.KinPositionDeviation: "Achse weicht vor Wiederanlauf vom Plan ab",
    AC.SeqStepTimeout: "Ablaufschritt hängt (Zeitüberwachung)",
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

# Schritte nach GRAFCET [Langfeld]; 20 und 30 sind die Ruheschritte
STEP_G2_TEXT = {0: "–", 20: "S20 Grundzustand – bereit", 21: "S21 Startposition anfahren",
                22: "S22 Niederhalter schließen", 23: "S23 Biegen (gekoppelt)",
                24: "S24 Halten unter Last – Bestätigung Q1", 25: "S25 Niederhalter öffnen – Bestätigung Q2",
                26: "S26 Biegekopf in Neutrallage", 27: "S27 Übergabe – Rohr entnehmen (ENT) oder fortsetzen",
                28: "S28 Grundstellung anfahren"}
STEP_G3_TEXT = {0: "–", 30: "S30 bereit", 31: "S31 Halt für Rotation", 32: "S32 W dreht"}


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
    """Erklärt nDetail einer Ablehnung (Konvention der SPS, siehe FB_HmiCommand und FB_PlanStore)."""
    if not detail:
        return ""
    if result == R.RejectedInterlock and detail & 0x10000:
        axes = [E_Axis(i).name for i in range(1, 6) if detail & (1 << i)]
        return "Achse nicht freigegeben oder gestört: " + ", ".join(axes)
    if result == R.RejectedInterlock and detail == 0x20000:
        return "Z1 steht hinter dem Start des Plans"
    try:
        return ALARM_TEXT[AC(detail)]
    except ValueError:
        pass
    if result == R.RejectedInterlock and detail < (1 << 10):
        bits = [BASE_STATE_BITS[i] for i in range(10) if detail & (1 << i)]
        if bits:
            return "; ".join(bits)
    if result == R.RejectedParam:
        return {1: "Zielposition außerhalb der Grenzen bzw. Plan-ID passt nicht",
                2: "Geschwindigkeit bzw. Planformat ungültig", 3: "Plan: Prüfsumme falsch (Übertragung unvollständig)",
                10: "Plan: z1_start ≥ z1_fin", 11: "Plan: s nicht streng monoton oder Abschnitte überlappen",
                12: "Plan: u oder φ außerhalb der Grenzen", 13: "Plan: Z2 außerhalb der Grenzen",
                14: "Plan: Z1-Ende hinter der Kollisionsgrenze", 15: "Plan: zu viele Stützpunkte",
                16: "Plan: X wäre bei diesem Vorschub zu schnell"}.get(detail, f"Detail {detail}")
    return f"Detail {detail}"
