# TwinCAT-Projekt einrichten

Stand: 25.11.2026. Gilt für TwinCAT 3.1 Build 4026 und das Projekt `plc/Biegbert/Biegbert.sln`.

Die SPS gibt es in **einer** Fassung für drei Einsätze:

| Einsatz | TwinCAT | Compiler-Define | Visualisierung |
|---|---|---|---|
| Simulation am Schreibtisch | NC-Achsen ohne Hardware, Peripherie in `FB_IoSim` | `SIM` | Anlagenersatz |
| Echte Anlage | Achsen an AX8000, E/A verknüpft | – | digitaler Zwilling |
| Tests | wie Simulation, zusätzlich TcUnit | `SIM, TEST` | – |

Das Backend ist in allen Fällen gleich (`python -m biegbert`, ADS). Die frühere SPS-Simulation im Backend gibt es nicht mehr.

> **Stand des Codes:** Die SPS-Quellen sind nicht in TwinCAT übersetzt worden, sondern außerhalb geschrieben und mit einem Strukturprüfer kontrolliert. Rechne beim ersten „Erstellen“ mit einzelnen Compilerfehlern (Tippfehler, Typumwandlungen, Bibliotheksnamen). Schick mir die Fehlerliste, dann korrigiere ich sie.

## 1  Was im Projekt liegt

| Ordner | Inhalt |
|---|---|
| `00_GVL` | `GVL_HMI` (Schnittstelle zum Backend, inkl. Planübergabe `stPlanXfer`), `GVL_Config` (Grenzen, Geometrie, Zeiten), `GVL_IO` (Prozessabbild und logische Signale), `GVL_Axis` (fünf `AXIS_REF`), `GVL_Devices` (Geräteinstanzen), `GVL_Plan` (aktiver Plan), `GVL_Sim` (Eingriffe der Simulation) |
| `01_DUT` | Enums und Strukturen; neu: `E_G0Step`, `E_G2Step`, `E_G3Step`, `ST_Plan*` |
| `02_IO` | `FB_IoMap` (einzige Stelle mit Zugriff auf das Prozessabbild), `FB_IoSim` (Niederhalter-Endlagen im Simulationsbetrieb) |
| `03_Devices` | `FB_Axis` (eine NC-Achse), `FB_Clamp` (ein Niederhalter), `F_Mod360` |
| `04_Kinematics` | `I_Kinematics`, `FB_BendKinematics` (NC Camming), `F_Z1Max`, `F_ChuckInZone`, `F_ClampBlocking` |
| `05_Sequence` | `FB_ModeManager` (G0, G1), `FB_BendSequence` (G2, G3), `FB_Interlocks` (Verriegelungen, Alarme) |
| `06_HMI` | `FB_HmiCommand` (Handshake, Heartbeat, Tippen), `FB_HmiStatus`, `FB_AlarmManager`, `FB_PlanStore`, `F_PlanChecksum` |
| `07_Test` | TcUnit-Tests (laufen mit `TEST`) |
| `POUs/MAIN` | Zyklus: Eingänge → Kommandos → Abläufe → Kinematik und Geräte → Ausgänge → Status |

Die Projektdatei `Biegbert_SPS.plcproj` ist bereits angepasst: alle neuen Dateien eingetragen, Bibliotheken `Tc2_MC2` und `Tc2_MC2_Camming` ergänzt, Compiler-Define `SIM` gesetzt.

## 2  Lizenzen

Für den Betrieb (auch in der Simulation) braucht TwinCAT:

| Lizenz | Wofür |
|---|---|
| TC1200 PLC | SPS |
| TF5000 NC PTP | NC-Achsen |
| TF5050 NC Camming | Kurvenscheiben (`MC_CamIn`, `MC_CamTableSelect`) |

Auf dem Entwicklungsrechner reichen 7-Tage-Testlizenzen: Projektmappe → SYSTEM → Lizenz → „7 Days Trial License …“. TF5050 muss eigens aktiviert werden (Reiter „Manage Licenses“, Haken bei TF5050).

## 3  Schritt für Schritt (Simulation)

### 3.1  Projekt öffnen und Bibliotheken prüfen

1. `plc/Biegbert/Biegbert.sln` öffnen.
2. Unter `Biegbert_SPS` → Verweise prüfen, dass `Tc2_MC2`, `Tc2_MC2_Camming`, `Tc2_Standard`, `Tc2_System`, `Tc3_Module` und `TcUnit` ohne Warnsymbol erscheinen. Fehlt eine, mit Rechtsklick → „Bibliothek hinzufügen“ eintragen.
3. Das SPS-Projekt braucht eine **referenzierte Task**, die `MAIN` aufruft (im SPS-Projektbaum unter `Biegbert_SPS Projekt`, Symbol mit Uhr). Fehlt sie: Rechtsklick `Biegbert_SPS Projekt` → Hinzufügen → „Referenzierte Task“ → `PlcTask` (SYSTEM → Tasks, 10 ms); dann Rechtsklick auf die neue Task → Hinzufügen → „Vorhandener Programmaufruf“ → `MAIN`. Ohne diese Task erscheinen keine Variablen im Prozessabbild und die Achsen lassen sich nicht verknüpfen. Tasks unter SYSTEM nur löschen, wenn sie im SPS-Projekt nicht referenziert sind.

### 3.2  NC-Achsen anlegen

Im Projekt gibt es bisher nur Z1. Unter MOTION → `NC-Task 1 SAF` → Achsen vier weitere Achsen anlegen (Rechtsklick → „Neues Element hinzufügen“ → Continuous Axis): **Z2, X, A, W**. Ohne Verknüpfung zur Hardware arbeiten sie als Simulationsachsen, genau wie dein vorhandenes Z1.

Je Achse unter „Parameter“ einstellen (Werte = `GVL_Config`, vorläufig):

| Achse | Einheit | Max. Geschwindigkeit | Beschleunigung/Verzögerung | Software-Endlagen | Bemerkung |
|---|---|---|---|---|---|
| Z1 | mm | 50 mm/s | 200 mm/s² | −30 / 1282 mm, überwachen | Master der Kurvenscheibe |
| Z2 | mm | 28,6 mm/s | 100 mm/s² | −50 / 48 mm | |
| X | mm | 3,5 mm/s | 20 mm/s² | −50 / 50 mm | Slave |
| A | ° | 180 °/s | 360 °/s² | −60 / 60 ° | Slave |
| W | ° | 180 °/s | 360 °/s² | keine | **kein Modulo** in der NC; die SPS rechnet die Anzeige auf 0–360° |

„Referenzgeschwindigkeit“ mindestens so groß wie die Maximalgeschwindigkeit setzen. In der Simulation die Schleppfehlerüberwachung ausgeschaltet lassen.

### 3.3  Achsen mit der SPS verknüpfen

1. SPS einmal **erstellen** (Rechtsklick `Biegbert_SPS` → Erstellen), damit `GVL_Axis` im Prozessabbild erscheint.
2. Je Achse: Achse → Reiter „Einstellungen“ → „Mit SPS verknüpfen …“ → passende Variable wählen:

| NC-Achse | SPS-Variable |
|---|---|
| Z1 | `GVL_Axis.Z1` |
| Z2 | `GVL_Axis.Z2` |
| X | `GVL_Axis.X` |
| A | `GVL_Axis.A` |
| W | `GVL_Axis.W` |

Falls Z1 noch mit einer Variablen aus dem alten Kopplungstest verknüpft ist, die Verknüpfung lösen und neu setzen.

### 3.4  Compiler-Define prüfen

Rechtsklick `Biegbert_SPS` → Eigenschaften → „Übersetzen“ → „Compiler-Defines“: dort steht `SIM`. Für die TcUnit-Tests `SIM, TEST` eintragen.

`SIM` bewirkt:

- Niederhalter-Endlagen, Klemmdruck und Not-Halt kommen aus `FB_IoSim` bzw. `GVL_Sim`, nicht aus den Klemmen,
- alle Ausgänge bleiben aus,
- `SimSetInput` und `SimInjectFault` sind erlaubt (Fehler-Buttons im HMI, Konsole `fault`/`input`),
- der Alarm „Simulationsbetrieb aktiv“ steht an, und `bSimulation` ist TRUE.

### 3.5  Aktivieren und starten

1. „Konfiguration aktivieren“ (Treppensymbol), Run-Modus bestätigen.
2. In der SPS einloggen, Programm laden, Start.
3. Kontrolle online: `GVL_HMI.stStatus.nLifeCounter` zählt hoch, `eMode = Pause`, `nStepG2 = 20`.

### 3.6  Backend und Browser

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
python -m biegbert
```

HMI `http://localhost:8765/`, Visualisierung `http://localhost:8765/vis/`. Im Log des HMI muss „Verbindung zur SPS hergestellt (SPS im Simulationsbetrieb)“ stehen. Meldet das Backend eine falsche Strukturgröße, passt `layout.py` nicht zur SPS (Größen: `stStatus` 495, `stAlarm` 777, `stPlanXfer` 103 552 Byte).

### 3.7  Erster Durchlauf

1. Kopfzeile: **Einzelachsen** (zweistufig).
2. Reiter 3 · Manuell: „Alle Achsen freigeben“.
3. **Grundstellung → Automatik** (geht nur, wenn alle Achsen auf 0 und alle Niederhalter offen sind).
4. Reiter 1 · Biegeplan: Plan laden → ① An SPS übertragen → ② Material bestätigen → ③ Start.
5. Ablauf: S21 Startposition, S22 Niederhalter schließen, S23 Biegen (Rotationen in G3), S24 Halten.
6. **Q1** (Niederhalter öffnen), **Q2** (Biegekopf in Neutrallage), in S27 **Rohr entnommen** → S28 fährt Z1, Z2, W auf 0 → S20.

Im Schrittbetrieb (Reiter 2) übernimmt das HMI Q1 und Q2 selbst und startet den nächsten Schritt als Fortsetzung aus S27.

## 4  Echte Anlage (Hardware-Tag)

### 4.1  Hardware einlesen

1. Intel-Netzwerkkarte mit TwinCAT-Echtzeittreiber, EtherCAT-Gerät anlegen, „Nach Boxen suchen“.
2. Erwartete Reihenfolge: AX8620 – AX8118 – AX8206 #1 – AX8206 #2 – EC1100 – EL1918 – EL2904 – ED1809 – ED2809 – EL2252 – EL6001 – EL6021 – EL9011; am zweiten Port des EC1100 EP1008 und EP2008.
3. Antriebe mit dem Drive Manager 2 einrichten (Motoren werden über OCT erkannt). Zuordnung:

| Modul | Kanal | Achse | Motor | Weg je Motorumdrehung |
|---|---|---|---|---|
| AX8118 | – | Z1 | AM8562 | 1,75 mm |
| AX8206 #1 | A | Z2 | AM8023 + WPLE40 i = 7 | 0,2143 mm (Gewinde prüfen) |
| AX8206 #1 | B | A | AM8532 + i = 100 | 3,6 ° |
| AX8206 #2 | A | X | AM8531 + i = 100 | 0,07 mm |
| AX8206 #2 | B | W | AM8031 + i = 100 | 3,6 ° |

4. Je NC-Achse: „Einstellungen“ → „Mit E/A verknüpfen …“ → passenden Antriebskanal wählen, Skalierung (Weg je Umdrehung) eintragen. Drehrichtungen prüfen; Vorzeichen von X und A mit der Visualisierung abgleichen.
5. Momentgrenzen als Antriebsparameter im AX8000 setzen (X, A, W nach Getriebe; Z1 materialabhängig), Schleppfehlerüberwachung einschalten.
6. Für das Ist-Moment je Achse das PDO „Torque actual value“ (0x6077) aktivieren und mit `GVL_IO.anTorqueIn[1..5]` verknüpfen (Index = Z1, Z2, X, A, W). Bezug ist das Nennmoment in `GVL_Config.aRatedTorque_Nm`.

### 4.2  E/A verknüpfen

| SPS-Variable | Klemme, Kanal | Signal |
|---|---|---|
| `GVL_IO.abClampOpenIn[1..6]` | EP1008, Kanal 1–6 | Endlage OFFEN Niederhalter 1–6 (SMT-8M) |
| `GVL_IO.bPressureOkIn` | EP1008, Kanal 7 | Druckschalter SDE5 |
| `GVL_IO.abClampValveOut[1..6]` | EP2008, Kanal 1–6 | Ventil Niederhalter 1–6, TRUE = schließen |
| `GVL_IO.bSafetyOkIn` | TwinSAFE-Ausgang an die Standard-SPS | Sicherheitskreis geschlossen, STO aufgehoben |
| `GVL_IO.bEStopReleasedIn` | TwinSAFE-Ausgang an die Standard-SPS | Not-Halt entriegelt |
| `GVL_IO.abPanelIn[1..16]` | ED1809 | Bedienfeld (Belegung offen) |
| `GVL_IO.abPanelOut[1..16]` | ED2809 | Leuchten (Belegung offen) |
| `GVL_IO.bCameraTriggerOut` | EL2252, Kanal 1 | Kamera (später) |

Die Nummerierung der Niederhalter (Paar 1 an der Führung) an der Anlage prüfen; sie muss zu `GVL_Config.aClampZoneLo_mm/Hi_mm` passen.

### 4.3  Simulation ausschalten

Compiler-Define `SIM` entfernen, neu erstellen, aktivieren. Danach meldet das HMI „SPS (real)“, und die Visualisierung wechselt in den digitalen Zwilling. Ohne `SIM` wirken `SimSetInput` und `SimInjectFault` nicht.

Tipp: Mit den TwinCAT-Projektvarianten lassen sich „Simulation“ und „Anlage“ als zwei Varianten pflegen (E/A-Geräte und Achsverknüpfungen je Variante, Compiler-Define je Variante). Das erspart das Umbauen vor jedem Wechsel.

### 4.4  Einmessen

Vor dem ersten Biegen: Null-Positionen aller Achsen setzen, `dZ0` (Abstand Futter–Führung) messen und `GVL_Config.fZ1CollisionBase_mm`, die Niederhalterzonen und `fVAtZ2Zero_mm` mit den gemessenen Werten ersetzen. Dieselben Werte in `backend/biegbert/config.py` eintragen (Datenfluss-Dokument, Kapitel 6).

## 5  Wie die SPS arbeitet (Kurzfassung)

- **Schnittstelle:** nur `GVL_HMI`. Kommandos über den Handshake `nCmdId`/`nAckId`, Tippen über `stJog`, Heartbeat 500 ms.
- **Betriebsarten (G1):** Start in Pause; Automatik und Einzelachsen nur zweistufig; Grundstellung → Automatik über Schritt 13. Not-Halt (G0) erzwingt Pause, Rückkehr nach Entriegeln und Quittieren.
- **Biegeablauf (G2, Schritte 20–28 nach Langfeld):** erster Start nur aus der Grundstellung; S21 Startposition und Kurvenscheibe laden, S22 Niederhalter, S23 Biegen, S24 Halten (Q1), S25 Niederhalter öffnen (Q2), S26 Neutrallage, S27 Übergabe (weiterer Abschnitt, ENT oder Fortsetzung am Rohr), S28 Grundstellung.
- **Kinematik:** eine Kurvenscheibe je Abschnitt (X und A an Z1), Geraden ohne Halt, Vorschub stückweise nach `feed_profile`, Halt nur an Rotationen (G3: W dreht) und am Ende.
- **Verriegelungen:** Abstand Futter–Führung, Futter in der Zone eines nicht offenen Niederhalters, Software-Endlagen, Achs- und Niederhalterfehler, Not-Halt (bricht den Ablauf ab).
- **Plan:** Backend schreibt `stPlanXfer` (Kopf mit Prüfsumme zuletzt), `PlanLoad` prüft und kopiert nach `GVL_Plan.stActive`.

Details, Zustandsdiagramme und Abweichungen von Langfeld: `04_Datenfluss_und_Zustaende.docx`, `docs/grafcet-zuordnung.md`.

## 6  Fehlersuche

| Symptom | Ursache und Abhilfe |
|---|---|
| Backend: „SPS nicht erreichbar“ | TwinCAT nicht im Run-Modus oder SPS nicht gestartet; bei `--ams` fehlt die ADS-Route |
| Backend: Strukturgröße passt nicht | DUT geändert, `layout.py` nicht nachgezogen (oder umgekehrt) |
| `AxisEnable` abgelehnt, Detail 1100 | Not-Halt aktiv (in der Simulation: Konsole `input 2 0`) |
| `SeqStart` abgelehnt, Bits 0–5 | erster Start nur aus der Grundstellung: Einzelachsen, Achsen auf 0 fahren, Niederhalter öffnen |
| `PlanLoad` abgelehnt, Detail 3 | Übertragung unvollständig, erneut „An SPS übertragen“ |
| `PlanLoad` abgelehnt, Detail 16 | X wäre zu schnell; Übergang verlängern oder Vorschub senken |
| Alarm „Kopplung oder Kurvenscheibe fehlgeschlagen“ | NC-Fehlercode im Detail; häufig TF5050 nicht lizenziert oder Achse nicht freigegeben |
| Ablauf hängt in S21 oder S28 | Achse nicht freigegeben oder gestört; nach 120 s Alarm „Ablaufschritt hängt“ |
