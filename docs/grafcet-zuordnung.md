# Zuordnung GRAFCET [Langfeld] ↔ SPS-Code

Stand: 25.11.2026. Die Schrittnummern im Code (`E_G0Step`, `E_G1`/`E_OperatingMode`, `E_G2Step`, `E_G3Step`) sind die GRAFCET-Nummern. Umsetzung als ST-Zustandsautomaten (E5).

## G0 Not-Halt – `FB_ModeManager`

| Langfeld | Code | Abweichung |
|---|---|---|
| 1 Betrieb | `S1_Betrieb` | – |
| 2 Not-Halt, Zwang G1{11} | `S2_NotHalt`, Betriebsart → Pause | Rückkehr bei entriegeltem Not-Halt, Safety bereit und Quittierung über `AlarmAck` |

Zusätzlich bricht `FB_BendSequence` einen laufenden Ablauf bei Not-Halt ab (→ S20), weil die Pneumatik entlüftet ist und die Klemmung nicht mehr gesichert ist (bei Langfeld als offener Punkt „Klemmprüfung nach Wiederanlauf“).

## G1 Betriebsarten – `FB_ModeManager`

| Langfeld | Code | Abweichung |
|---|---|---|
| 10 Automatik (FREI) | `Automatic`, `bFree` | – |
| 11 Pause | `Pause` (Startzustand) | auch bei jedem neuen Alarm ≥ Stop und Heartbeat-Ausfall |
| RES1/RES2 | `ModeResume1/2` | zweite Stufe innerhalb 10 s; verlangt HMI lebt, kein Not-Halt, kein Fault |
| MAN1/MAN2 → 12 | `ModeSingleAxis1/2` → `SingleAxis` | laufender Ablauf wird abgebrochen (Kopplung gelöst, G2 → 20) |
| OK bei GZ_POS → 13 | `ModeReturnToBase` → `ReturnToBase` | 13 dauert einen Zyklus: G2 := 20, G3 := 30, dann 10 |

## G2 Biegevorgang – `FB_BendSequence`

| Langfeld | Code | Abweichung |
|---|---|---|
| 20 Grundzustand, ST bei GZ·¬ERR·SAFE·PRG·MAT | `S20_Grundzustand`, `SeqStart` | Bedingungen in `FB_HmiCommand`; GZ = Bits 0–5 von `dwBaseStateMissing` |
| 21 Z1_EIL auf Z1_start | `S21_Startposition` | fährt zusätzlich Z2 (Plan), X und A auf 0; lädt die Kurvenscheibe des Abschnitts |
| 22 NH := NH_soll, warten auf NH_ist | `S22_Niederhalter` | Niederhalter, deren Zone das Futter gerade berührt, bleiben offen |
| 23 Z1_BIEG + AX_BIEG, NH öffnen bei z_NH,i | `S23_Biegen` | Kopplung über Kurvenscheibe inkl. An- und Rückbiegen (Prüfbericht H4); Geraden zwischen Bögen ohne Halt; Z2 steht (H5) |
| 24 HALTEN, Q1 | `S24_Halten`, `SeqConfirmQ1` | – |
| 25 NH := 0, Q2 | `S25_NhOeffnen`, `SeqConfirmQ2` | Q2 wirkt erst, wenn alle Niederhalter offen gemeldet sind |
| 26 AX_NEUTRAL | `S26_Neutral` | löst zuerst die Kopplung |
| 27 n := n + 1; n < N → 21; sonst ENT | `S27_Uebergabe` | zusätzlich **Fortsetzung am selben Rohr**: in S27 darf ein neuer Plan geladen und mit `SeqStart` ab der aktuellen Z1-Position gestartet werden (Schrittbetrieb des HMI). Erster Start bleibt an die Grundstellung gebunden |
| 28 Z1, Z2, W auf 0 | `S28_Grundstellung` | fährt auch X und A auf 0; W auf das nächste Vielfache von 360° |

Ein Abschnitt n ist eine Kurvenscheibe mit konstantem Z2. Ein Plan besteht im Normalfall aus einem Abschnitt; mehrere Abschnitte (N > 1) entstehen nur, wenn Z2 zwischen Bögen wechselt.

## G3 Werkstückrotation – `FB_BendSequence`

| Langfeld | Code | Abweichung |
|---|---|---|
| 30 Bereit | `S30_Bereit` | – |
| p_Z1 ≥ Z1_rot,k → 31 Stop | `S31_Stop` | Die Kinematik fährt den Master genau bis Z1_rot,k und meldet `AtRotation()` |
| 32 Drehen W_ROT | `S32_Drehen` | W dreht relativ um `w_deg`; danach `ContinueAfterRotation()` |

## Zeitüberwachung

Nicht bei Langfeld: S21, S26 und S28 dürfen höchstens 120 s, S22 höchstens 10 s dauern; sonst Alarm `SeqStepTimeout` (Stop) mit der Schrittnummer als Detail.
