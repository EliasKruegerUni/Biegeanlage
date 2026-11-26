# Biegeanlage
Programmierung der Miniatur-Biegeanlage inkl. Simulation

## Hardware-Anforderungen:

Die Anlage wird über einen externen PC bedient. Die SPS läuft auf einem abgekapselten Kern und kommuniziert per EtherCAT mit der EtherCAT-Klemme, die Inputs und Outputs managed.
dafür ist nötig: 
- Windows 10/11
- Intel Netzwerkkarte (siehe: https://infosys.beckhoff.com/index.php?content=../content/1031/tc3_overview/9309844363.html&id=)

## Requirements:

infos liegen in docs/umgebung.txt


## HMI und Backend starten

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
python -m biegbert            # TwinCAT lokal; --ams <AMS-Net-ID> für den Anlagen-PC
```

Voraussetzung: TwinCAT im Run-Modus mit gestarteter SPS `Biegbert_SPS` (Simulation über das Compiler-Define `SIM`).
Einrichtung des TwinCAT-Projekts: `docs/twincat-einrichtung.md`.

Dann im Browser `http://localhost:8765/` (HMI) und `http://localhost:8765/vis/` (3D-Visualisierung).
Details: `docs/hmi-backend.md`. Tests: `pytest` im Wurzelordner.
