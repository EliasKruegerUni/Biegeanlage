# Kinematik

Stand: 25.11.2026. Grundlage ist Entscheidung E6.

## Ziel

Der Ablauf kennt nur `I_Kinematics`. Die erste Umsetzung `FB_BendKinematics` nutzt NC Camming mit Z1 als Master. Eine spätere Umsetzung mit externen Sollwerten ersetzt nur diesen Baustein.

`FB_BendKinematics` lädt je Planabschnitt zwei Kurvenscheiben (X und A über Z1, nicht äquidistant, absolut), koppelt mit `MC_CamIn` und fährt den Master mit `MC_MoveAbsolute` stückweise nach dem Vorschubprofil bis zur nächsten Rotation bzw. zum Abschnittsende.

## Interface

| Methode | Rückgabe | Wirkung | Zulässig in |
|---|---|---|---|
| `Cyclic()` | – | Einmal pro SPS-Zyklus | immer |
| `LoadPlan(nPlanId)` | `BOOL` | Plan laden | `Idle`, `Loaded` |
| `Start()` | `BOOL` | An der aktuellen Masterposition koppeln, Master starten | `Loaded` |
| `Hold()` | `BOOL` | Master gesteuert anhalten | `Running` |
| `Resume()` | `BOOL` | Master fortsetzen | `Held` |
| `Abort()` | `BOOL` | Master stoppen, im Stillstand entkoppeln | `Coupling`, `Running`, `Holding`, `Held` |
| `Reset()` | `BOOL` | Fehler quittieren, Plan bleibt erhalten | `Fault` |
| `GetStatus()` | `ST_KinStatus` | Status lesen | immer |
| `LoadSection(n)` | `BOOL` | Kurvenscheiben des Abschnitts n laden; fertig, wenn `TableReady()` | `Loaded` |
| `AtRotation()`, `RotationAngle()` | `BOOL`, `LREAL` | Master steht an einer Rotation; Drehwinkel für W | `Held` |
| `ContinueAfterRotation()` | `BOOL` | nach der Drehung von W weiterfahren | `Held` an Rotation |
| `SectionDone()` | `BOOL` | Master am Abschnittsende, Kopplung besteht | `Held` |
| `Unload()`, `IsCoupled()` | `BOOL` | Plan verwerfen; Kopplung aktiv | entkoppelt; immer |

Methoden nehmen nur Aufträge an. `TRUE` heißt nicht, dass die Bewegung fertig ist. Das Ergebnis steht im Status.

## Zustände

![Zustände der Kinematik](abb/kinematik_zustaende.png)

| Modus | Zustände | Bedeutung |
|---|---|---|
| `Single` | `Idle`, `Loaded`, `Decoupling`, `Fault` | Keine laufende Kopplung. Einzelachsbewegung über `FB_Axis` nur in `Idle` und `Loaded` |
| `Coupled` | `Coupling`, `Running` | Master läuft oder fährt an |
| `Hold` | `Holding`, `Held` | Master bremst oder steht, Kopplung bleibt |

## Zuordnung der Achsen

| Achse | Rolle |
|---|---|
| Z1 | Master |
| X | Slave, Tabelle u(s) |
| A | Slave, Tabelle φ(s), unabhängig von X |
| W | Einzelbewegung in G3, simultaner Betrieb später als weiterer Slave |
| Z2 | Während des Bogens fest, Wert je Biegung |

Für Z2 gilt die Empfehlung aus Prüfbericht H5. Sie ist noch mit Thomas zu bestätigen.

## Verhalten

| Ereignis | Kinematik | Ablauf | Wiederanlauf |
|---|---|---|---|
| Pause | `Hold()`, Slaves folgen dem Master | G2 und G3 eingefroren | Zweistufig, dann `Resume()` |
| G3 Rotation | `Hold()`, nach `Held` dreht W | G2 eingefroren | `Resume()` |
| Warnung | Keine Änderung | Anzeige | – |
| Alarm `Stop` | `Hold()` | Pause | Wie Pause |
| Achsfehler Master | Wechsel nach `Fault`, Master stoppen | Pause | `Reset()`, Prüfung, zweistufige Bestätigung, `Start()` |
| Achsfehler Slave | Master sofort stoppen, `Fault` | Pause | `Reset()`, Prüfung, zweistufige Bestätigung, `Start()` |
| GUI-Ausfall | `Hold()` | Pause | Nach Rückkehr wie Pause |
| Not-Halt | Antriebe durch STO abgeschaltet, `Fault` | G0 erzwingt Pause, Programmzustand bleibt | Quittierung, `Reset()`, Prüfung, zweistufige Bestätigung, `Start()` |

## Wiederanlauf nach Not-Halt oder Fehler

Der Ablauf ruft `Start()` nur auf, wenn alle Bedingungen erfüllt sind:

1. Safety ist wieder bereit, und alle Achsen sind fehlerfrei.
2. Die benötigten Niederhalter wurden neu geklemmt, denn die Pneumatik wurde entlüftet.
3. X, A, W und Z2 liegen zur Masterposition innerhalb der Toleranz. A und W sind nicht selbsthemmend und können sich bewegt haben.
4. Der Bediener hat den Wiederanlauf zweistufig bestätigt.

Bei zu großer Abweichung wird `KinPositionDeviation` gesetzt. Eine automatische Ausgleichsbewegung gibt es nicht, weil sie das belastete Rohr verformen könnte.

`Reset()` behält den Plan, damit das Programm am unterbrochenen Schritt fortgesetzt werden kann (S2). `Start()` koppelt dann an der aktuellen Masterposition. Ob die NC das nach STO genau so erlaubt, wird in Phase 8 geprüft.

## Ergebnisse der NC-Tests

Stand: 05.11.2026. Simulation mit Z1 als Master, X als Slave, Tabelle mit Masterweg 0 bis 120 mm und X bis 7 mm. Die Ergebnisse gelten nur für diese Simulation und den geprüften Fall.

| Test | Ergebnis |
|---|---|
| Tabelle laden | 10000 Zeilen fehlerfrei, unter 1 s. Das Maximum der NC wurde nicht bestimmt. |
| Normallauf | X folgt dem Plan: X 5,25 mm/s bei Vorgabe 30 mm/s für Z1, größter Schritt 0,05 mm pro SPS-Zyklus |
| Halt und Fortsetzen | Fortsetzen über neuen Fahrbefehl, kein Sprung, kein Fehler |
| Override | Override von Z1 wirkt über die Kopplung auf X, X bleibt synchron |
| Harter Halt bei Z1 ≈ 20 mm | `Deceleration` 500: keine höheren Spitzenwerte, kein Fehler |
| Freigabe von X im Lauf entzogen | Fehler `0x4260`, Z1 stoppt von selbst nach etwa 6 mm. X stand dabei im Plateau. |
| Entkoppeln im Lauf | X läuft mit konstanter Geschwindigkeit weiter, im Test bis 170 mm |
| Tabelle im gekoppelten Zustand laden | Abgelehnt mit `0x4a26` |
| Wiederanlauf mit Abweichung | Nur Kopplung geprüft: X koppelte bei 10 mm statt 7 mm ohne Fehler und Sprung. Das Weiterfahren ist **nicht geklärt**. |

## Vorgaben für FB_BendKinematics

- `Abort()` stoppt zuerst den Master und entkoppelt erst im Stillstand. Nach `MC_CamOut` wird der Slave zusätzlich gestoppt.
- `LoadPlan()` lädt nur im entkoppelten Zustand.
- `Cyclic()` überwacht in jedem Zyklus den Fehlercode aller Achsen in `NcToPlc`. Bausteinausgänge reichen nicht: Beim Freigabetest zeigten `MC_CamIn` und `MC_Power` keinen Fehler.
- `Resume()` wartet auf den Stillstand und gibt einen neuen Fahrbefehl an den Master.
- Befehle an Z1 kommen nur, wenn kein anderer Auftrag läuft.
- Aufrufe von `MC_CamIn` warten auf `Done` von `MC_CamTableSelect`.

## Noch zu prüfen

- Wiederanlauf mit abweichendem Slave: Folgt der Slave dem Plan, mit Versatz oder gar nicht?
- Wiederanlauf nach Fehler mit `MC_Reset` und neuer Kopplung, auch mit `MC_CamInfo`
- Fehler des Slaves in einer Rampe: Wie groß wird die Abweichung beim Stoppen?
- Verhalten nach echtem STO an der Anlage

## Biegeplan

Anforderungen an das Format:

| Bereich | Inhalt |
|---|---|
| Kopf | Formatversion, Plan-ID, Name, Erstellungsdaten |
| Material | Durchmesser, Wandstärke, Werkstoff, Rp0,2, Kraftgrenze |
| Stützstellen | Vorschubweg s mit u(s), φ(s), optional W(s) |
| Je Biegung | Z1_start, Z1_fin, Z2, NH_soll |
| Niederhalter | Schaltpunkt z_NH,i je Niederhalter |
| Rotation | Z1_rot,k und W_rot,k |

Regeln:
- Der Plan ist eine Funktion des Vorschubwegs s, und s steigt streng monoton.
- Anbiegen, stationärer Bereich und Rückbiegen liegen im gekoppelten Abschnitt (H4).
- X und A haben eigene Verläufe. Ihr Verhältnis ist nicht fest (Dok. 01, Abschnitt 4).
- Z2 ist je Biegung konstant (H5).
- Einheiten sind mm und °.

Vor dem Laden werden geprüft:
- Verfahrwege und Software-Endlagen
- Geschwindigkeit und Beschleunigung aller Achsen, vor allem X (K4)
- Kraftgrenze aus dem Material (H2)
- Niederhalter-Schaltpunkte, Vorlauf und freie Rohrlänge (H3, H8)
- Abstand dZ12
- Netto-Drehung von W (H6)

Beispiel für die Struktur, keine freigegebenen Prozesswerte:

```json
{
  "format": 1,
  "plan_id": 1,
  "name": "Beispiel",
  "material": {
    "diameter_mm": 15.0,
    "wall_mm": 2.0,
    "yield_mpa": 300.0,
    "force_limit_n": 16000.0
  },
  "bends": [
    {
      "z1_start_mm": 100.0,
      "z1_fin_mm": 220.0,
      "z2_mm": 0.0,
      "clamps": [1, 2, 3, 4, 5, 6],
      "clamp_open_mm": [150.0, 150.0, 180.0, 180.0, 210.0, 210.0],
      "points": [
        {"s_mm": 100.0, "u_mm": 0.0, "phi_deg": 0.0},
        {"s_mm": 140.0, "u_mm": 7.0, "phi_deg": 32.52},
        {"s_mm": 180.0, "u_mm": 7.0, "phi_deg": 32.52},
        {"s_mm": 220.0, "u_mm": 0.0, "phi_deg": 0.0}
      ],
      "rotations": []
    }
  ]
}
```
