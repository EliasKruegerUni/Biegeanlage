# HMI und Backend

Stand: 25.11.2026. Die SPS-Simulation im Backend ist entfernt; das Backend spricht nur noch über ADS mit TwinCAT (echte Anlage oder TwinCAT im Simulationsbetrieb, siehe `twincat-einrichtung.md`). Ergänzt `hmi-schnittstelle.md` (SPS-Seite) um die Seite Browser ↔ Python.

## Starten

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
python -m biegbert                     # TwinCAT auf diesem Rechner (lokale AMS-Net-ID)
python -m biegbert --ams <AMS-Net-ID>  # TwinCAT auf dem Anlagen-PC (ADS-Route nötig)
```

| Adresse | Inhalt |
|---|---|
| `http://localhost:8765/` | HMI |
| `http://localhost:8765/vis/` | 3D-Visualisierung, zeigt die Istwerte aus der SPS |
| `http://localhost:8765/api/state` | Status, Alarme und Pläne als JSON |

Tests: `pytest` im Repo-Wurzelordner (`pytest.ini` setzt `backend` in den Pfad).

## Aufbau

| Ordner/Datei | Aufgabe |
|---|---|
| `hmi/` | Bedienoberfläche: `index.html`, `style.css`, `app.js`, ohne Build-Schritt |
| `backend/biegbert/server.py` | FastAPI: HMI und Visualisierung ausliefern, WebSockets |
| `backend/biegbert/bridge.py` | Handshake, Heartbeat, Status, Alarme, Pläne, Debug-Konsole |
| `backend/biegbert/plc/hmi_types.py` | Spiegel der Enums und Strukturen aus `01_DUT` |
| `backend/biegbert/plc/layout.py` | Byte-Layout `pack_mode 1`, Lesen ganzer Strukturen über ADS |
| `backend/biegbert/plc/ads.py` | SPS über pyads, Zugriff nur auf `GVL_HMI.*`, Planübergabe nach `stPlanXfer` |
| `backend/biegbert/plan/` | Biegeplan-Modell, LRA-Import, Prozessmodell (Geometrie → Maschinenplan) |
| `backend/biegbert/config.py` | Achsgrenzen, Geschwindigkeiten, Geometrie, Zeiten; vorläufige Werte markiert |
| `backend/biegbert/alarms.py` | Klartexte für Alarme, Ergebnisse und Ablaufschritte |

Die Bridge kennt nur `PlcLink`. Läuft TwinCAT nicht, versucht sie alle 2 s neu zu verbinden und synchronisiert danach den Kommandozähler (`nCmdId := nAckId`).

## Betriebswege im HMI

| Weg | Reiter | Ablauf |
|---|---|---|
| 1 CAD/Biegeplan | 1 · Biegeplan | Datei laden (biegbert-geo, Maschinenplan Format 1, LRA/YBC). Das Backend berechnet den Maschinenplan. Dann ① `PlanLoad`, ② `MaterialConfirm`, ③ `SeqStart`; am Ende `SeqConfirmQ1` (S24), `SeqConfirmQ2` (S25) und `SeqPartRemoved` (S27, Rohr entnommen). STEP-Import folgt |
| 2 Halbautomatisch | 2 · Halbautomatisch | Elemente eingeben: Gerade, Bogen mit konstantem Radius, Bogen mit veränderlichem Radius, Rotation. „Als Folge“ erzeugt einen Plan für Weg 1. „Schritt für Schritt“ plant und startet jeweils das nächste Element ab der aktuellen Z1-Position. Der erste Schritt startet aus der Grundstellung, jeder weitere als Fortsetzung am selben Rohr aus S27 |
| 3 Vollmanuell | 3 · Manuell | Betriebsart Einzelachsen. Freigabe, Fahrbefehle, Tippen, Vorschub starten/stoppen, Niederhalter, „A tangential zu X“ |

Ein veränderlicher Radius entsteht in Weg 1 und 2 durch die κ(s)-Tabelle. In Weg 3 entsteht er, indem X während des laufenden Vorschubs verstellt wird.

## WebSocket-Protokoll `/ws` (Version 1)

Alle Nachrichten sind JSON-Objekte mit `type`. Anfragen mit `id` erhalten eine Antwort `{"type":"reply","id":…,"ok":…,"text":…}`.

### Browser → Backend

| `type` | Felder | Wirkung |
|---|---|---|
| `ping` | `t` | Heartbeat, alle 100 ms. Antwort `pong` |
| `cmd` | `cmd` (Name aus `E_HmiCmd`), `axis` (Z1…W), `clamp`, `position`, `velocity`, `value`, `quiet` | Kommando über den Handshake. Antwort mit `result`, `detail`, `detail_text`, `ms` |
| `jog` | `axis`, `dir` (−1/0/+1), `factor` (%) | `stCmd.stJog`, ohne Handshake |
| `param` | `param`: `plan_id`, `diameter_mm`, `wall_mm`, `yield_mpa`, `force_limit_n`, `feed_override_pct`, `single_axis_velo_pct` | `stParam` schreiben, dann `ParamApply` |
| `plan.upload` | `filename`, `content`, `default_radius` | Plan parsen und berechnen |
| `plan.build` | `plan` (biegbert-geo) | Plan berechnen |
| `plan.get` | `plan_id` | Plan mit Bericht abrufen |
| `plan.load` | `plan_id` | Plan übergeben, `PlanLoad`, Material nach `stParam` |
| `step.run` | `elements`, `process`, `material` | Schrittbetrieb: planen, laden, bestätigen, starten |
| `debug` | `text` | Konsole, `help` listet die Befehle |

### Backend → Browser

| `type` | Inhalt |
|---|---|
| `hello` | Version, SPS-Art, Achsgrenzen, Geometrie, Zeiten, Kommandoliste, letzte Logeinträge, Pläne |
| `status` | 20 Hz: Betriebsart, Ablaufschritte, Achsen, Niederhalter, Kinematik, aktive Parameter, Grundstellung, letzte Kommandoantwort |
| `alarms` | Bei Änderung von `nChangeCounter`: Liste mit Klartext, Stufe, Quelle, Zeit, aktiv/quittiert |
| `log` | Ereignisprotokoll für die Konsole (`level`: debug, info, warning, error) |

### Visualisierung `/`

Die 3D-Visualisierung (`frontend/simulation.html`) verbindet sich mit `ws://<host>:8765/`. Unter `/vis/` liefert das Backend sie selbst aus. Etwa 30-mal pro Sekunde erhält sie:

```json
{"type": "axes",
 "values":  {"Z1": …, "Z2": …, "X": …, "A": …, "W": …, "NH1": …, "NH2": …, "NH3": …},
 "set":     {"Z1": …, "Z2": …, "X": …, "A": …, "W": …},
 "process": {"simulation": true, "plc_kind": "ads", "plc_ok": true, "mode": "Automatic",
             "kin": "Running", "plan_id": 3, "bend": 1, "step_g2": 23, "step_g2_text": "…"}}
```

Die Visualisierung meldet nichts an die SPS zurück. Slider-Bewegungen in der Visualisierung ignoriert das Backend.

## Simulation

Simuliert wird nur noch in TwinCAT: NC-Achsen ohne Hardware und die Peripherie (Niederhalter, Druck, Not-Halt) im Baustein `FB_IoSim`, aktiv mit dem Compiler-Define `SIM`. Das Backend und das HMI sind in Simulation und Realbetrieb identisch; die SPS meldet den Simulationsbetrieb in `bSimulation`.

| Betriebsart der Visualisierung | Achswerte | Slider | Rohr | Automatische Wahl |
|---|---|---|---|---|
| Eigenständig | Slider, Biegeversuch mit eigenem Vorschub | aktiv | „Starte Versuch“ setzt Z1 = 0 und legt das Rohr ein | keine Daten vom Backend (Doppelklick ohne Backend) |
| Anlagenersatz | TwinCAT mit `SIM` | gesperrt | neues Rohr beim Start aus S20, bleibt bei Fortsetzung aus S27, Teilansicht bei ENT | Daten mit `simulation = true` |
| Digitaler Zwilling | Istwerte der realen Anlage | gesperrt | wie Anlagenersatz; zusätzlich Soll/Ist-Tabelle | Daten mit `simulation = false` |

## Heartbeat

Wie in `hmi-schnittstelle.md`: Der Browser pingt alle 100 ms. Der Takt kommt aus einem Web Worker, weil Browser Timer in Hintergrund-Tabs drosseln. Das Backend erhöht `nHeartbeat` nur, solange ein Browser innerhalb von 300 ms gepingt hat. Wird das HMI geschlossen, bleibt der Heartbeat aus, die SPS meldet nach 500 ms `HmiHeartbeatLost`, stoppt Bewegungen und geht von Automatik in Pause.

## Maschinenplan

Format wie in `kinematik.md`, Abschnitt Biegeplan. Ein Plan ist **ein Abschnitt** = eine Kurvenscheibe von `z1_start_mm` bis `z1_fin_mm`; Geraden sind Teil der Kurvenscheibe (u = 0), dadurch hält der Vorschub zwischen Bögen nicht. Mehrere Abschnitte sind nur bei wechselndem Z2 vorgesehen (noch nicht erzeugt). Erweiterungen je Abschnitt:

| Feld | Bedeutung |
|---|---|
| `feed_profile` | `[{"z1_mm", "feed_mm_s"}]`, höchstens 8 Stufen. In Bögen senkt das Prozessmodell den Vorschub so weit, dass X höchstens 95 % von 3,5 mm/s braucht; Geraden laufen mit dem Plan-Vorschub |
| `feed_mm_s` | kleinster Vorschub des Profils (Anzeige) |
| `rotations` | `[{"z1_mm", "w_deg"}]`, höchstens 16; der Master hält dort, W dreht relativ (G3), dann geht es weiter |
| `clamps`, `clamp_open_mm` | zu Beginn zu schließende Niederhalter und Z1-Position, ab der jeder öffnet |

Übergabe an die SPS: `layout.pack_plan` → `GVL_HMI.stPlanXfer` (103 552 Byte), Kopf mit Prüfsumme zuletzt; die SPS prüft beim `PlanLoad` (`FB_PlanStore`) und meldet Fehler als `nDetail` 1–16. Höchstens 4000 Stützpunkte.

Prozessmodell, vorläufig: u(R) = R − √(R² − v²), φ = a_factor · arcsin(v/R), lineare Rampe von u über `transition_mm` an jeder Elementgrenze, an Rotationen und Planenden abgeschnitten. Keine Rückfederungskorrektur.

## Offene Punkte

- ADS-Anbindung an der echten SPS messen: Zeiten für Handshake und Heartbeat, Notifications statt Polling
- Winkelverlust durch die lineare u-Rampe (Umbau auf winkelerhaltende Rampe vorgeschlagen)
- Z2 je Bogen im Prozessmodell (Format und SPS können es bereits)
- STEP-Import
- Benutzerrollen (Bediener/Einrichter) im Backend
