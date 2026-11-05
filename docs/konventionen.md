# Namens- und Codekonventionen

Stand: 05.11.2026. Grundlage ist Entscheidung E10.

## Präfixe

| Element | Muster | Beispiel |
|---|---|---|
| Funktionsbaustein | `FB_` | `FB_Axis` |
| Funktion | `F_` | `F_CalcBendRadius` |
| Interface | `I_` | `I_Kinematics` |
| Struktur | `ST_` | `ST_HmiCmd` |
| Enum | `E_` | `E_G2Step` |
| Globale Variablenliste | `GVL_` | `GVL_HMI` |
| Instanz eines FB | `fb` | `fbAxisZ1` |
| Interface-Variable | `ip` | `ipKinematics` |
| Struktur-Variable | `st` | `stStatus` |
| Enum-Variable | `e` | `eStep` |
| Boolean | `b` | `bEnable` |
| Ganzzahl | `n` | `nCmdId` |
| Fließkomma | `f` | `fPosZ1_mm` |
| Array | `a`, bei Bool `ab` | `aAxis`, `abPanelIn` |
| Zeit | `t` | `tHeartbeatTimeout` |
| Bitmaske | `dw` | `dwBaseStateMissing` |
| Konstante | `c` | `cAxisCount` |

Ausnahme: Zeitkonstanten behalten `t`, sonst wäre der Typ nicht sichtbar.

## Achsen und Einheiten

- Achsen heißen im Code nur Z1, Z2, X, A und W. „B-Achse“ wird nicht verwendet.
- Längen in mm, Winkel in °, Geschwindigkeiten in mm/s oder °/s, Kräfte in N, Momente in Nm, Drücke in bar.
- Zeiten für Parameter in ms, sonst als Typ `TIME`.
- Die Einheit steht im Namen, wenn sie nicht aus der Achse folgt: `fTorque_Nm`, `fForceLimit_N`.
- Generische Achsfelder wie `fActPos` nutzen mm oder °, je nach Achse.

## GRAFCET-Bezug

- Schritte heißen `S<Nummer>_<Name>`, zum Beispiel `E_G2Step.S23_Biegen`.
- Die Enum-Werte entsprechen den GRAFCET-Nummern.
- Abweichungen von [Langfeld] werden in Phase 8 in `docs/grafcet-zuordnung.md` begründet.

## Code

- Enums erhalten `qualified_only` und `strict`.
- HMI-Strukturen erhalten `pack_mode := '1'`, damit Python die Lage kennt.
- `GVL_HMI` wird nur nach den Regeln aus `architektur.md` genutzt.
- Bausteine erhalten eine kurze Kopfzeile mit ihrem Zweck.
- Keine Zahlen ohne Bedeutung im Code. Grenzwerte liegen in `GVL_Config` oder im Parametersatz.
- Dateinamen entsprechen dem Objektnamen.
- Die SPS-Sprache für Abläufe ist ST.

## Python

- Formatierung und Benennung nach PEP 8.
- Einheiten stehen wie in der SPS im Namen, wenn sie nicht aus der Achse folgen.
- Das Backend greift nur auf `GVL_HMI.*` zu.
