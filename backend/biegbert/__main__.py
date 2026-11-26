"""Start: python -m biegbert [--ams 5.80.201.232.1.1] [--port 8765]

Voraussetzung: TwinCAT läuft (Run-Modus) und die SPS „Biegbert_SPS“ ist gestartet – auf diesem Rechner
(Standard: lokale AMS-Net-ID) oder auf dem Anlagen-PC (--ams). Ob die SPS simuliert (Compiler-Define SIM)
oder die echte Anlage steuert, meldet sie selbst.

Aus dem Ordner backend/ starten (oder backend/ im PYTHONPATH). Danach im Browser:
  http://localhost:8765/       HMI
  http://localhost:8765/vis/   3D-Visualisierung (zeigt die Istwerte)
"""
from __future__ import annotations

import argparse
import logging

import uvicorn

from .config import Settings
from .server import create_app


def main():
    ap = argparse.ArgumentParser(prog="biegbert", description="Biegbert-Backend (HMI ↔ SPS)")
    ap.add_argument("--ams", default="127.0.0.1.1.1", help="AMS-Net-ID der SPS (Standard: lokales TwinCAT)")
    ap.add_argument("--ams-port", type=int, default=851)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 = im Netz erreichbar")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--log", default="info")
    a = ap.parse_args()
    logging.basicConfig(level=a.log.upper(), format="%(asctime)s %(levelname)-7s %(message)s")
    s = Settings(plc="ads", ams_net_id=a.ams, ams_port=a.ams_port, host=a.host, port=a.port)
    uvicorn.run(create_app(s), host=a.host, port=a.port, log_level="warning", ws_ping_interval=None)


if __name__ == "__main__":
    main()
