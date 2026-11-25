# HMI und Backend

Stand: 24.11.2026, erste Fassung. Ergänzt `hmi-schnittstelle.md` (SPS-Seite) um die Seite Browser ↔ Python.

## Starten

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
python -m biegbert              # SPS-Simulation
python -m biegbert --plc ads --ams <AMS-Net-ID>   # echte SPS (noch ungetestet)
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
| `backend/biegbert/plc/ads.py` | Echte SPS über pyads, Zugriff nur auf `GVL_HMI.*` |
| `backend/biegbert/plc/sim.py` | SPS-Simulation mit dem Verhalten von `GVL_HMI` |
| `backend/biegbert/plan/` | Biegeplan-Modell, LRA-Import, Prozessmodell (Geometrie → Maschinenplan) |
| `backend/biegbert/config.py` | Achsgrenzen, Geschwindigkeiten, Geometrie, Zeiten; vorläufige Werte markiert |
| `backend/biegbert/alarms.py` | Klartexte für Alarme, Ergebnisse und Ablaufschritte |

Die Bridge kennt nur `PlcLink`. Simulation und ADS sind austauschbar, ohne dass HMI oder Bridge sich ändern.

## Betriebswege im HMI

| Weg | Reiter | Ablauf |
|---|---|---|
| 1 CAD/Biegeplan | 1 · Biegeplan | Datei laden (biegbert-geo, Maschinenplan Format 1, LRA/YBC). Das Backend berechnet den Maschinenplan. Dann ① `PlanLoad`, ② `MaterialConfirm`, ③ `SeqStart`, am Ende `SeqConfirmQ1`. STEP-Import folgt |
| 2 Halbautomatisch | 2 · Halbautomatisch | Elemente eingeben: Gerade, Bogen mit konstantem Radius, Bogen mit veränderlichem Radius, Rotation. „Als Folge“ erzeugt einen Plan für Weg 1. „Schritt für Schritt“ plant und startet jeweils das nächste Element ab der aktuellen Z1-Position |
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

Die 3D-Visualisierung (`frontend/index.html`) verbindet sich mit `ws://<host>:8765/`. Unter `/vis/` liefert das Backend sie selbst aus. Etwa 30-mal pro Sekunde erhält sie:

```json
{"type": "axes",
 "values":  {"Z1": …, "Z2": …, "X": …, "A": …, "W": …, "NH1": …, "NH2": …, "NH3": …},
 "set":     {"Z1": …, "Z2": …, "X": …, "A": …, "W": …},
 "process": {"simulation": true, "plc_kind": "sim", "plc_ok": true, "mode": "Automatic",
             "kin": "Running", "plan_id": 3, "bend": 1, "step_g2": 23, "step_g2_text": "…"}}
```

Die Visualisierung meldet nichts an die SPS zurück. Slider-Bewegungen in der Visualisierung ignoriert das Backend.

## Simulation: zwei getrennte Dinge

| | Was wird ersetzt? | Wo | Wählen |
|---|---|---|---|
| **SPS-Simulation** | die SPS (TwinCAT) | `backend/biegbert/plc/sim.py` | `python -m biegbert` (Standard) statt `--plc ads` |
| **Betriebsarten der Visualisierung** | – (Anzeige) | `frontend/index.html` | automatisch oder Umschalter in der Seitenleiste |

Betriebsarten der Visualisierung:

| Betriebsart | Achswerte | Slider | Rohr | Automatische Wahl |
|---|---|---|---|---|
| Eigenständig | Slider, Biegeversuch mit eigenem Vorschub | aktiv | „Starte Versuch“ setzt Z1 = 0 und legt das Rohr ein | keine Daten vom Backend (Doppelklick ohne Backend) |
| Anlagenersatz | von der SPS: TwinCAT mit simulierten Achsen oder SPS-Simulation | gesperrt | wird bei Ablaufstart (G2 20–23) automatisch eingelegt, folgt Z1/X/A/W | Daten mit `simulation = true` |
| Digitaler Zwilling | Istwerte der realen Anlage | gesperrt | wie Anlagenersatz; zusätzlich Soll/Ist-Tabelle | Daten mit `simulation = false` |

Mögliche Kombinationen: HMI-Entwicklung ohne TwinCAT (SPS-Simulation + Anlagenersatz), Test der echten SPS-Logik (TwinCAT mit simulierten Achsen über ADS + Anlagenersatz), Betrieb an der Anlage (TwinCAT real über ADS + Zwilling).

## Heartbeat

Wie in `hmi-schnittstelle.md`: Der Browser pingt alle 100 ms. Der Takt kommt aus einem Web Worker, weil Browser Timer in Hintergrund-Tabs drosseln. Das Backend erhöht `nHeartbeat` nur, solange ein Browser innerhalb von 300 ms gepingt hat. Wird das HMI geschlossen, bleibt der Heartbeat aus, die SPS meldet nach 500 ms `HmiHeartbeatLost`, stoppt Bewegungen und geht von Automatik in Pause.

## Maschinenplan

Format wie in `kinematik.md`, Abschnitt Biegeplan, mit zwei Erweiterungen:

| Feld je Biegung | Bedeutung |
|---|---|
| `feed_mm_s` | Vorschub für diesen Abschnitt. Das Prozessmodell senkt ihn, wenn X sonst schneller als 3,5 mm/s fahren müsste |
| `rotations` | `[{"z1_mm", "w_deg"}]`, relative Drehung von W bei Erreichen von `z1_mm` (G3) |

Gerade Strecken zwischen zwei Biegungen fährt die SPS ungekoppelt beim Anfahren der Startposition. s steigt innerhalb einer Biegung streng monoton; aufeinanderfolgende Biegungen dürfen sich einen Grenzpunkt teilen.

Prozessmodell, vorläufig: u(R) = R − √(R² − v²), φ = a_factor · arcsin(v/R), lineare Rampe von u über `transition_mm` an jeder Elementgrenze, an Rotationen und Planenden abgeschnitten. Keine Rückfederungskorrektur.

## Simulation: Festlegungen, die in die SPS übernommen oder ersetzt werden müssen

Die Simulation braucht Festlegungen, die in der SPS noch fehlen. Sie sind hier gesammelt, damit sie bei der SPS-Programmierung bewusst übernommen oder geändert werden.

| Thema | Festlegung in der Simulation |
|---|---|
| Startbetriebsart | `Pause` |
| Planübergabe | `transfer_plan()` als Ersatz für den Übergabebereich aus Phase 9; die ADS-Anbindung lehnt das ab |
| `SeqStart` | Automatik, Plan geladen, Material bestätigt, kein Fault, alle Achsen freigegeben, Z1 ≤ Start der ersten Biegung |
| G2-Schritte | 20 Startposition, 21 Niederhalter schließen, 23 Biegen, 24 Ende halten (wartet auf `SeqConfirmQ1`), 25 Lösen und Biegekopf zurück |
| G3-Schritte | 30 Halten, 31 W dreht |
| `nDetail` bei Ablehnung | Alarmcode der Verriegelung (z. B. 1001, 1100, 3002, 4000), Bitmaske `dwBaseStateMissing` (Bit 6 = Fault aktiv), Prüfcode beim Planladen (10–14), `0x10000` + Bit je `E_Axis` = Achse nicht freigegeben oder gestört, `0x20000` = Z1 hinter dem Start der ersten Biegung |
| `SimInjectFault` `nValue` | 1 Achsfehler Z1, 2 X, 3 A, 4 Druckabfall, 5 Not-Halt, 6 Schleppfehler-Warnung Z1 |
| `SimSetInput` `nValue` | 1 Druckschalter, 2 Not-Halt (`fPosition` ≠ 0 = aktiv) |
| Niederhalter-Nummern | Paar k = Niederhalter 2k−1 und 2k, Paar 1 liegt an der Führung (an der Anlage prüfen) |
| Niederhalter-Schaltzeit | 120 ms |

## Offene Punkte

- ADS-Anbindung gegen die SPS testen: Strukturgrößen (495 und 777 Byte), Schreibreihenfolge, Notifications statt Polling
- Übergabebereich für Pläne in `GVL_HMI` (Phase 9) und Behandlung im Schrittbetrieb
- Festlegungen aus der Tabelle oben in der SPS bestätigen oder ändern
- STEP-Import
- Benutzerrollen (Bediener/Einrichter) im Backend
