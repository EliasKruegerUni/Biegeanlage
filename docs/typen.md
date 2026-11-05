# Enums und zentrale Typen

Stand: 05.11.2026. Vorhanden heißt: Datei liegt im SPS-Projekt.

## Enums

| Typ | Zweck | Stand |
|---|---|---|
| `E_Axis` | Achsen, Index für `aAxis` | vorhanden |
| `E_OperatingMode` | Betriebsart G1, Werte 10 bis 13 | vorhanden |
| `E_AxisState` | Achszustand für die GUI | vorhanden |
| `E_ClampState` | Niederhalterzustand | vorhanden |
| `E_HmiCmd` | Kommandos | vorhanden |
| `E_CmdResult` | Ergebnis der Kommandoprüfung | vorhanden |
| `E_AlarmCode` | Alarmcodes | vorhanden |
| `E_AlarmSeverity` | Reaktionsstufen | vorhanden |
| `E_KinState` | Zustand der Kinematik | vorhanden |
| `E_MotionMode` | Modus Single, Coupled, Hold | vorhanden |
| `E_G0Step`, `E_G2Step`, `E_G3Step` | Schritte der Abläufe | Phase 8 |

## Strukturen

| Typ | Zweck | Stand |
|---|---|---|
| `ST_HmiCmd`, `ST_HmiJog` | Kommandos und Tippen | vorhanden |
| `ST_HmiCmdAck` | Antwort auf Kommandos | vorhanden |
| `ST_HmiStatus` | Gesamtstatus | vorhanden |
| `ST_HmiAxisStatus` | Status je Achse | vorhanden |
| `ST_HmiClampStatus` | Status je Niederhalter | vorhanden |
| `ST_HmiIoStatus` | E/A-Übersicht | vorhanden, Belegung offen |
| `ST_HmiParam`, `ST_Material` | Parameter | vorhanden |
| `ST_HmiAlarms`, `ST_AlarmEntry` | Alarme | vorhanden |
| `ST_KinStatus` | Status der Kinematik | vorhanden |
| `ST_BendPlan` | Biegeplan in der SPS | Phase 8/9 |

## Bausteine und Interfaces

| Name | Ordner | Stand |
|---|---|---|
| `I_Kinematics` | `04_Kinematics` | vorhanden |
| `FB_BendKinematics` | `04_Kinematics` | Rumpf ohne Achsbewegung |
| `FB_Axis` | `03_Devices` | Phase 3/6 |
| `FB_Clamp` | `03_Devices` | Phase 3/6 |
| `FB_IoMap` | `02_IO` | Phase 3 |
| `FB_HmiCommand`, `FB_AlarmManager` | `06_HMI` | Phase 4/6 |
