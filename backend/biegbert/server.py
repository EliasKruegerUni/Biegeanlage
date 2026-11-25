"""FastAPI-Server.

  GET  /            HMI (Ordner hmi/)
  WS   /ws          HMI-Protokoll (siehe docs/hmi-backend.md)
  GET  /vis/        3D-Visualisierung (Ordner frontend/, simulation.html), verbindet sich mit WS /
  WS   /            Achswerte für die Visualisierung (Protokoll der Visualisierung: {"type":"axes"})
  GET  /api/state   Status, Alarme und Pläne als JSON (Diagnose)
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .bridge import Bridge
from .config import Settings
from .plan import PlanError, geo_from_json
from .plc import create_link

log = logging.getLogger("biegbert")


async def _send(ws: WebSocket, msg: dict):
    try:
        await ws.send_text(json.dumps(msg, ensure_ascii=False, allow_nan=False, default=str))
    except Exception:  # noqa: BLE001 - Client weg; Aufräumen übernimmt der Empfangszweig
        pass


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    root = Path(__file__).resolve().parents[2]
    hmi_dir = Path(settings.hmi_dir) if settings.hmi_dir else root / "hmi"
    vis_dir = Path(settings.vis_dir) if settings.vis_dir else root / "frontend"
    bridge = Bridge(create_link(settings.plc, settings.ams_net_id, settings.ams_port))
    bridge.set_sender(_send)

    @asynccontextmanager
    async def lifespan(_app):
        await bridge.start()
        yield
        await bridge.stop()

    app = FastAPI(title="Biegbert-Backend", lifespan=lifespan)
    app.state.bridge = bridge

    @app.get("/api/state")
    async def state():
        return JSONResponse({"status": bridge.status_json, "alarms": bridge.alarms_json,
                             "plans": [bridge._plan_summary(p) for p in bridge.plans.values()]})

    @app.websocket("/ws")
    async def ws_hmi(ws: WebSocket):
        await ws.accept()
        name = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
        bridge.hmi_connected(ws, name)
        await _send(ws, bridge.hello())
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                t = msg.get("type")
                if t == "ping":
                    bridge.ping(ws)
                    await _send(ws, {"type": "pong", "t": msg.get("t")})
                    continue
                bridge.ping(ws)
                asyncio.get_running_loop().create_task(_handle(ws, msg))
        except WebSocketDisconnect:
            pass
        except Exception as e:  # noqa: BLE001
            bridge.log("error", "HMI", f"WebSocket-Fehler: {e}")
        finally:
            await bridge.hmi_disconnected(ws)

    async def _handle(ws: WebSocket, msg: dict):
        t, rid = msg.get("type"), msg.get("id")
        try:
            if t == "cmd":
                r = await bridge.command(msg.get("cmd"), axis=msg.get("axis") or 0, clamp=int(msg.get("clamp") or 0),
                                         position=float(msg.get("position") or 0), velocity=float(msg.get("velocity") or 0),
                                         value=int(msg.get("value") or 0), quiet=bool(msg.get("quiet")))
            elif t == "jog":
                await bridge.jog(msg.get("axis", ""), int(msg.get("dir", 0)), float(msg.get("factor", 0)))
                return
            elif t == "param":
                r = await bridge.apply_param(msg.get("param", {}))
            elif t == "plan.upload":
                rec = bridge.plan_upload(msg.get("filename", "plan.json"), msg.get("content", ""),
                                         float(msg.get("default_radius", 45)))
                r = {"ok": rec["machine"] is not None, "plan": rec}
            elif t == "plan.build":
                rec = bridge.plan_from_geo(geo_from_json(msg.get("plan", {})))
                r = {"ok": rec["machine"] is not None, "plan": rec}
            elif t == "plan.load":
                r = await bridge.plan_load(int(msg.get("plan_id")))
            elif t == "plan.get":
                rec = bridge.plans.get(int(msg.get("plan_id")))
                r = {"ok": bool(rec), "plan": rec}
            elif t == "step.run":
                r = await bridge.step_run(msg.get("elements", []), msg.get("process", {}), msg.get("material", {}))
            elif t == "debug":
                r = await bridge.debug(msg.get("text", ""))
            else:
                r = {"ok": False, "text": f"Unbekannter Nachrichtentyp „{t}“"}
        except PlanError as e:
            bridge.log("error", "Plan", str(e))
            r = {"ok": False, "text": str(e)}
        except Exception as e:  # noqa: BLE001
            bridge.log("error", "Backend", f"Fehler bei „{t}“: {e!r}")
            r = {"ok": False, "text": f"Interner Fehler: {e}"}
        if rid is not None:
            await _send(ws, {"type": "reply", "id": rid, **r})

    @app.websocket("/")
    async def ws_vis(ws: WebSocket):
        """Die Visualisierung verbindet sich standardmäßig mit ws://<host>:8765/."""
        await ws.accept()
        bridge.vis_clients.add(ws)
        bridge.log("info", "Visualisierung", "3D-Visualisierung verbunden (zeigt Istwerte)")
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("type") == "manual":
                    bridge.log("debug", "Visualisierung", "Slider in der Visualisierung bewegt – wird ignoriert, "
                                                          "Achsen kommen von der SPS")
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            bridge.vis_clients.discard(ws)

    if vis_dir.is_dir():
        vis_page = "simulation.html" if (vis_dir / "simulation.html").exists() else "index.html"

        @app.get("/vis")
        @app.get("/vis/")
        async def vis_root():
            return RedirectResponse(f"/vis/{vis_page}")

        app.mount("/vis", StaticFiles(directory=vis_dir, html=True), name="vis")
    if hmi_dir.is_dir():
        app.mount("/", StaticFiles(directory=hmi_dir, html=True), name="hmi")
    else:
        log.warning("HMI-Ordner %s fehlt", hmi_dir)
    return app
