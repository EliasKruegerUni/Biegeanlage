"""Bridge zwischen HMI-Clients (WebSocket) und SPS (PlcLink).

Aufgaben (docs/hmi-schnittstelle.md):
  - Handshake: ein offenes Kommando, nCmdId zuletzt, max. 1 s auf nAckId warten, nicht wiederholen
  - Heartbeat: nHeartbeat alle 100 ms erhöhen, aber nur solange ein Browser lebt (Ping < 300 ms)
  - Status zyklisch lesen (20 Hz), Lebenszeichen nLifeCounter überwachen, Neustart der SPS erkennen
  - Alarme bei Änderung lesen, Klartexte und Historie liefern
  - Biegepläne parsen, ins Maschinenformat übersetzen und an die SPS übergeben
  - Ereignisprotokoll (Konsole im HMI)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict
from typing import Any, Callable

from . import PROTOCOL_VERSION, __version__
from .alarms import ALARM_TEXT, RESULT_TEXT, STEP_G2_TEXT, STEP_G3_TEXT, detail_text, source_text
from .config import AXES, AXIS_ORDER, GEOMETRY, TIMING
from .plan import GeoPlan, PlanError, build, element_from_json, parse_upload
from .plan.model import Material, Process
from .plc.base import PlcLink
from .plc.hmi_types import (
    BASE_STATE_BITS, E_AlarmCode, E_AlarmSeverity, E_Axis, E_AxisState, E_ClampState, E_CmdResult,
    E_HmiCmd, E_KinState, E_MotionMode, E_OperatingMode, ST_HmiParam, ST_HmiStatus, ST_Material, enum_name,
)

log = logging.getLogger("biegbert")
LEVELS = ("debug", "info", "warning", "error")


def _filetime_to_unix(ft: int) -> float:
    return ft / 10_000_000 - 11644473600 if ft else 0.0


class Bridge:
    def __init__(self, link: PlcLink):
        self.link = link
        self.hmi_clients: dict[Any, dict] = {}      # websocket -> {"last_ping": t, "name": str}
        self.vis_clients: set = set()
        self._send: Callable[[Any, dict], Any] | None = None
        self._cmd_lock = asyncio.Lock()
        self._cmd_id = 0
        self._hb = 0
        self._browser_alive = False
        self.status: ST_HmiStatus | None = None
        self.status_json: dict = {}
        self.alarms_json: dict = {"list": [], "highest": 0, "overflow": False}
        self._alarm_counter = -1
        self._alarm_keys: dict[tuple, dict] = {}
        self._life_last = None
        self._life_t = 0.0
        self.plc_ok = False
        self.logbuf: deque = deque(maxlen=1000)
        self.plans: dict[int, dict] = {}
        self._next_plan_id = 1
        self._tasks: list[asyncio.Task] = []
        self._jog_active = False

    # ── Lebenszyklus ────────────────────────────────────────────────────
    async def start(self):
        await asyncio.to_thread(self.link.connect)
        st = await asyncio.to_thread(self.link.read_status)
        self._cmd_id = st.stCmdAck.nAckId          # Beim Start nCmdId auf nAckId setzen
        await asyncio.to_thread(self.link.write_cmd_id, self._cmd_id)
        self.log("info", "Backend", f"Biegbert-Backend {__version__} gestartet, SPS-Anbindung: {self.link.kind}")
        if self.link.kind == "sim":
            self.log("warning", "Backend", "SPS-Simulation aktiv – keine echte Steuerung verbunden")
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._poll_loop()), loop.create_task(self._heartbeat_loop()),
                       loop.create_task(self._vis_loop())]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.to_thread(self.link.close)

    def set_sender(self, fn):
        self._send = fn

    # ── Protokoll ───────────────────────────────────────────────────────
    def log(self, level: str, source: str, text: str, data: Any = None):
        e = {"type": "log", "t": time.time(), "level": level, "source": source, "text": text}
        if data is not None:
            e["data"] = data
        self.logbuf.append(e)
        getattr(log, level if level in LEVELS else "info")("[%s] %s", source, text)
        self.broadcast(e)

    def broadcast(self, msg: dict, vis: bool = False):
        if not self._send:
            return
        for ws in list(self.vis_clients if vis else self.hmi_clients):
            asyncio.get_event_loop().create_task(self._send(ws, msg))

    def hello(self) -> dict:
        return {
            "type": "hello", "version": __version__, "protocol": PROTOCOL_VERSION, "plc": self.link.kind,
            "axes": {n: {"unit": a.unit, "min": a.min_pos if a.min_pos != float("-inf") else None,
                         "max": a.max_pos if a.max_pos != float("inf") else None,
                         "v_max": a.v_max, "v_setup": a.v_setup, "modulo": a.modulo} for n, a in AXES.items()},
            "geometry": {"v_at_z2_zero_mm": GEOMETRY.v_at_z2_zero_mm, "z1_collision_base_mm": GEOMETRY.z1_collision_base_mm,
                         "clamp_zones": [GEOMETRY.clamp_zone_z1(p) for p in (1, 2, 3)]},
            "timing": asdict(TIMING),
            "commands": {c.name.rstrip("_"): int(c) for c in E_HmiCmd},
            "log": list(self.logbuf)[-200:],
            "status": self.status_json, "alarms": self.alarms_json,
            "plans": [self._plan_summary(p) for p in self.plans.values()],
        }

    # ── Clients ─────────────────────────────────────────────────────────
    def hmi_connected(self, ws, name: str):
        self.hmi_clients[ws] = {"last_ping": time.monotonic(), "name": name}
        self.log("info", "HMI", f"Bedienung verbunden ({name}), {len(self.hmi_clients)} aktiv")

    async def hmi_disconnected(self, ws):
        info = self.hmi_clients.pop(ws, None)
        if self._jog_active:
            await self.jog("Z1", 0, 0)
        if info:
            self.log("warning", "HMI", f"Bedienung getrennt ({info['name']}), {len(self.hmi_clients)} aktiv")

    def ping(self, ws):
        if ws in self.hmi_clients:
            self.hmi_clients[ws]["last_ping"] = time.monotonic()

    # ── Zyklische Aufgaben ──────────────────────────────────────────────
    async def _heartbeat_loop(self):
        period = TIMING.plc_heartbeat_ms / 1000
        while True:
            now = time.monotonic()
            alive = any((now - c["last_ping"]) * 1000 < TIMING.browser_timeout_ms for c in self.hmi_clients.values())
            if alive != self._browser_alive:
                self._browser_alive = alive
                self.log("info" if alive else "warning", "Backend",
                         "Browser-Heartbeat vorhanden – zähle nHeartbeat" if alive
                         else "Kein Browser-Heartbeat – nHeartbeat wird nicht mehr erhöht (SPS stoppt nach 500 ms)")
            if alive:
                self._hb = (self._hb + 1) & 0xFFFFFFFF
                try:
                    await asyncio.to_thread(self.link.write_heartbeat, self._hb)
                except Exception as e:  # noqa: BLE001
                    self.log("error", "ADS", f"Heartbeat schreiben fehlgeschlagen: {e}")
            await asyncio.sleep(period)

    async def _poll_loop(self):
        period = TIMING.status_poll_ms / 1000
        while True:
            try:
                st = await asyncio.to_thread(self.link.read_status)
                self._check_life(st)
                self.status = st
                self.status_json = self._status_to_json(st)
                self.broadcast(self.status_json)
                al = await asyncio.to_thread(self.link.read_alarms)
                if al.nChangeCounter != self._alarm_counter:
                    self._alarm_counter = al.nChangeCounter
                    self._update_alarms(al)
                if not self.plc_ok:
                    self.plc_ok = True
                    self.log("info", "SPS", "Verbindung zur SPS hergestellt")
            except Exception as e:  # noqa: BLE001
                if self.plc_ok:
                    self.log("error", "SPS", f"Lesen von GVL_HMI fehlgeschlagen: {e}")
                self.plc_ok = False
                self.status_json = {**self.status_json, "type": "status", "plc": {"kind": self.link.kind, "ok": False}}
                self.broadcast(self.status_json)
                await asyncio.sleep(1.0)
            await asyncio.sleep(period)

    def _check_life(self, st: ST_HmiStatus):
        now = time.monotonic()
        lc = st.nLifeCounter
        if self._life_last is not None:
            if lc < self._life_last and self._life_last - lc > 1000:
                self.log("warning", "SPS", "nLifeCounter zurückgesprungen – SPS neu gestartet, synchronisiere Kommandozähler")
                self._cmd_id = st.stCmdAck.nAckId
            if lc == self._life_last:
                if now - self._life_t > 1.0 and self.plc_ok:
                    self.log("error", "SPS", "nLifeCounter steht seit > 1 s – SPS läuft nicht?")
                    self._life_t = now + 1e9
            else:
                self._life_t = now
        else:
            self._life_t = now
        self._life_last = lc

    async def _vis_loop(self):
        """Achswerte für die 3D-Visualisierung (frontend/index.html, Protokoll „axes“)."""
        period = TIMING.vis_rate_ms / 1000
        open_mm = {E_ClampState.Open: 60.0, E_ClampState.Opening: 35.0, E_ClampState.Closing: 35.0,
                   E_ClampState.Clamped: 10.5, E_ClampState.Fault: 35.0, E_ClampState.Unknown: 60.0}
        while True:
            if self.vis_clients and self.status:
                st = self.status
                vals = {n: st.aAxis[i].fActPos for i, n in enumerate(AXIS_ORDER)}
                sets = {n: st.aAxis[i].fSetPos for i, n in enumerate(AXIS_ORDER)}
                for d in (vals, sets):
                    if d["W"] > 180:
                        d["W"] -= 360
                for k in (1, 2, 3):
                    a, b = st.aClamp[2 * k - 2], st.aClamp[2 * k - 1]
                    vals[f"NH{k}"] = min(open_mm.get(a.eState, 60.0), open_mm.get(b.eState, 60.0))
                sj = self.status_json
                process = {"simulation": st.bSimulation, "plc_kind": self.link.kind, "plc_ok": self.plc_ok,
                           "mode": sj.get("mode"), "kin": sj.get("kin", {}).get("state"),
                           "plan_id": st.stKin.nPlanId, "bend": st.nBend,
                           "step_g2": st.nStepG2, "step_g2_text": sj.get("step_g2_text")}
                self.broadcast({"type": "axes", "values": vals, "set": sets, "process": process}, vis=True)
            await asyncio.sleep(period)

    # ── Status in JSON ──────────────────────────────────────────────────
    def _status_to_json(self, st: ST_HmiStatus) -> dict:
        axes = {}
        for i, n in enumerate(AXIS_ORDER):
            a = st.aAxis[i]
            axes[n] = {"state": enum_name(E_AxisState, a.eState), "enabled": a.bEnabled, "ready": a.bReady,
                       "error": a.bError, "error_id": a.nErrorId, "pos": a.fActPos, "set_pos": a.fSetPos,
                       "vel": a.fActVelo, "set_vel": a.fSetVelo, "lag": a.fLagError, "torque": a.fTorque_Nm}
        p = st.stParamActive
        return {
            "type": "status", "t": time.time(),
            "plc": {"kind": self.link.kind, "ok": True, "life": st.nLifeCounter},
            "browser_alive": self._browser_alive, "hmi_alive": st.bHmiAlive, "simulation": st.bSimulation,
            "estop": st.bEStopActive, "safety_ok": st.bSafetyOk,
            "mode": enum_name(E_OperatingMode, st.eMode),
            "step_g2": st.nStepG2, "step_g2_text": STEP_G2_TEXT.get(st.nStepG2, f"S{st.nStepG2}"),
            "step_g3": st.nStepG3, "step_g3_text": STEP_G3_TEXT.get(st.nStepG3, f"S{st.nStepG3}"),
            "bend": st.nBend,
            "base_missing": [BASE_STATE_BITS[b] for b in range(10) if st.dwBaseStateMissing & (1 << b)],
            "ack": {"id": st.stCmdAck.nAckId, "result": enum_name(E_CmdResult, st.stCmdAck.eResult),
                    "detail": st.stCmdAck.nDetail},
            "axes": axes,
            "clamps": [{"state": enum_name(E_ClampState, c.eState), "valve": c.bValve, "open": c.bOpen,
                        "t_ms": c.fLastSwitchTime_ms} for c in st.aClamp],
            "io": {"pressure_ok": st.stIo.bPressureOk},
            "kin": {"state": enum_name(E_KinState, st.stKin.eState), "mode": enum_name(E_MotionMode, st.stKin.eMode),
                    "plan_id": st.stKin.nPlanId, "error": st.stKin.bError, "error_id": st.stKin.nErrorId,
                    "master": st.stKin.fMasterPos_mm},
            "param": {"plan_id": p.nPlanId, "diameter_mm": p.stMaterial.fDiameter_mm, "wall_mm": p.stMaterial.fWall_mm,
                      "yield_mpa": p.stMaterial.fYield_MPa, "force_limit_n": p.stMaterial.fForceLimit_N,
                      "feed_override_pct": p.fFeedOverride_pct, "single_axis_velo_pct": p.fSingleAxisVelo_pct},
            "z1_max": GEOMETRY.z1_max(st.aAxis[1].fActPos),
        }

    def _update_alarms(self, al):
        items, keys = [], {}
        for slot, e in enumerate(al.aEntry[:al.nCount], 1):
            if not e.eCode:
                continue
            code = int(e.eCode)
            try:
                name = E_AlarmCode(code).name
                text = ALARM_TEXT.get(E_AlarmCode(code), name)
            except ValueError:
                name, text = f"Code {code}", f"Unbekannter Alarm {code}"
            src = source_text(code, e.nSource)
            item = {"slot": slot, "code": code, "name": name, "text": text, "source": src,
                    "severity": enum_name(E_AlarmSeverity, e.eSeverity), "detail": e.nDetail,
                    "time": _filetime_to_unix(e.nTimestamp), "active": e.bActive, "acked": e.bAcked}
            items.append(item)
            keys[(code, e.nSource)] = item
        for k, it in keys.items():
            old = self._alarm_keys.get(k)
            label = f"{it['text']}{' (' + it['source'] + ')' if it['source'] else ''}"
            lvl = {"Info": "info", "Warning": "warning"}.get(it["severity"], "error")
            if old is None or (it["active"] and not old["active"]):
                self.log(lvl, "Alarm", f"{it['severity']}: {label}" + (f", Detail {it['detail']:#x}" if it["detail"] else ""))
            elif old["active"] and not it["active"]:
                self.log("info", "Alarm", f"Ursache behoben: {label}" + ("" if it["acked"] else " – Quittierung offen"))
        for k, old in self._alarm_keys.items():
            if k not in keys:
                self.log("debug", "Alarm", f"Eintrag entfernt: {old['text']}")
        self._alarm_keys = keys
        self.alarms_json = {"type": "alarms", "list": items, "highest": enum_name(E_AlarmSeverity, al.eHighestSeverity),
                            "overflow": al.bOverflow, "counter": al.nChangeCounter}
        self.broadcast(self.alarms_json)

    # ── Kommandos (Handshake) ───────────────────────────────────────────
    async def command(self, cmd: str | int, axis: str | int = 0, clamp: int = 0, position: float = 0.0,
                      velocity: float = 0.0, value: int = 0, source: str = "HMI", quiet: bool = False) -> dict:
        try:
            e_cmd = E_HmiCmd[cmd] if isinstance(cmd, str) else E_HmiCmd(int(cmd))
        except (KeyError, ValueError):
            return {"ok": False, "result": "BackendReject", "text": f"Unbekanntes Kommando „{cmd}“"}
        try:
            e_axis = E_Axis[axis] if isinstance(axis, str) and axis else E_Axis(int(axis or 0))
        except (KeyError, ValueError):
            return {"ok": False, "result": "BackendReject", "text": f"Unbekannte Achse „{axis}“"}
        label = e_cmd.name + (f" {e_axis.name}" if e_axis else "") + (f" NH{clamp}" if clamp else "")
        if e_cmd == E_HmiCmd.AxisMoveAbs:
            label += f" → {position:g} @ {velocity:g}"
        if e_cmd in (E_HmiCmd.SimSetInput, E_HmiCmd.SimInjectFault) and self.status and not self.status.bSimulation:
            return {"ok": False, "result": "BackendReject", "text": "Nur im Simulationsbetrieb erlaubt"}
        async with self._cmd_lock:
            ack = await asyncio.to_thread(self.link.read_ack)
            if ack.nAckId != self._cmd_id:
                txt = f"{label}: vorheriges Kommando {self._cmd_id} noch offen (nAckId {ack.nAckId})"
                self.log("error", source, txt)
                return {"ok": False, "result": "Busy", "text": txt}
            await asyncio.to_thread(self.link.write_cmd_fields, int(e_cmd), int(e_axis), int(clamp),
                                    float(position), float(velocity), int(value))
            new_id = (self._cmd_id + 1) & 0xFFFFFFFF
            await asyncio.to_thread(self.link.write_cmd_id, new_id)
            self._cmd_id = new_id
            t0 = time.monotonic()
            while True:
                ack = await asyncio.to_thread(self.link.read_ack)
                if ack.nAckId == new_id:
                    break
                if (time.monotonic() - t0) * 1000 > TIMING.ack_timeout_ms:
                    txt = f"{label}: keine Antwort der SPS nach {TIMING.ack_timeout_ms} ms (nicht wiederholt)"
                    self.log("error", source, txt)
                    return {"ok": False, "result": "Timeout", "text": txt, "cmd_id": new_id}
                await asyncio.sleep(0.01)
        dt_ms = (time.monotonic() - t0) * 1000
        res = E_CmdResult(ack.eResult) if ack.eResult in E_CmdResult._value2member_map_ else E_CmdResult.None_
        ok = res == E_CmdResult.Accepted
        det = detail_text(res, ack.nDetail)
        txt = f"{label}: {RESULT_TEXT.get(res, res.name)}" + (f" – {det}" if det else "") + f" ({dt_ms:.0f} ms)"
        if not quiet or not ok:
            self.log("info" if ok else "warning", source, txt)
        return {"ok": ok, "result": res.name.rstrip("_"), "detail": ack.nDetail, "detail_text": det, "text": txt,
                "cmd_id": new_id, "ms": round(dt_ms, 1)}

    async def jog(self, axis: str, direction: int, factor: float):
        e_axis = E_Axis[axis] if axis in E_Axis.__members__ else E_Axis.None_
        direction = max(-1, min(1, int(direction)))
        await asyncio.to_thread(self.link.write_jog, int(e_axis), direction, float(factor))
        if direction and not self._jog_active:
            self.log("debug", "HMI", f"Tippen {axis} {'+' if direction > 0 else '−'} ({factor:.0f} %)")
        self._jog_active = direction != 0

    async def apply_param(self, d: dict) -> dict:
        p = ST_HmiParam(int(d.get("plan_id", 0)),
                        ST_Material(float(d.get("diameter_mm", 15)), float(d.get("wall_mm", 2)),
                                    float(d.get("yield_mpa", 300)), float(d.get("force_limit_n", 16000))),
                        float(d.get("feed_override_pct", 100)), float(d.get("single_axis_velo_pct", 100)))
        await asyncio.to_thread(self.link.write_param, p)
        return await self.command("ParamApply")

    # ── Biegepläne ──────────────────────────────────────────────────────
    def _plan_summary(self, p: dict) -> dict:
        return {"plan_id": p["plan_id"], "name": p["name"], "kind": p["kind"], "ok": p["report"].get("ok", True),
                "summary": p["report"].get("summary", {}), "created": p["created"]}

    def _store(self, kind: str, name: str, geo: GeoPlan | None, machine: dict | None, report: dict) -> dict:
        pid = self._next_plan_id
        self._next_plan_id += 1
        if machine:
            machine["plan_id"] = pid
        rec = {"plan_id": pid, "name": name, "kind": kind, "geo": geo.to_json() if geo else None,
               "machine": machine, "report": report, "created": time.time()}
        self.plans[pid] = rec
        return rec

    def current_z1(self) -> float:
        return self.status.aAxis[0].fActPos if self.status else 0.0

    def plan_from_geo(self, geo: GeoPlan, name: str | None = None, z1_start: float | None = None) -> dict:
        z1 = self.current_z1() if z1_start is None else z1_start
        machine, report = build(geo, self._next_plan_id, z1)
        rec = self._store("geo", name or geo.name, geo, machine, report)
        n_err = sum(m["level"] == "error" for m in report["messages"])
        n_warn = sum(m["level"] == "warning" for m in report["messages"])
        self.log("info" if machine else "error", "Plan",
                 f"Plan {rec['plan_id']} „{rec['name']}“ berechnet: {report.get('summary', {}).get('bends', 0)} Bögen, "
                 f"{n_err} Fehler, {n_warn} Warnungen (Start Z1 = {z1:.1f} mm)")
        for m in report["messages"]:
            self.log("warning" if m["level"] == "warning" else "error" if m["level"] == "error" else "info", "Plan",
                     ("Element " + str(m["element"] + 1) + ": " if m.get("element") is not None else "") + m["text"])
        return rec

    def plan_upload(self, filename: str, content: str, default_radius: float = 45.0) -> dict:
        kind, obj = parse_upload(filename, content, default_radius)
        if kind == "geo":
            return self.plan_from_geo(obj, name=f"{filename}")
        machine = obj
        rep = {"ok": True, "messages": [], "elements": [],
               "summary": {"machine_sections": len(machine["bends"]),
                           "points": sum(len(b.get("points", [])) for b in machine["bends"]),
                           "z1_start_mm": machine["bends"][0]["z1_start_mm"] if machine["bends"] else 0,
                           "z1_end_mm": machine["bends"][-1]["z1_fin_mm"] if machine["bends"] else 0}}
        rec = self._store("machine", machine.get("name", filename), None, machine, rep)
        self.log("info", "Plan", f"Maschinenplan {rec['plan_id']} „{rec['name']}“ geladen (keine Prozessmodellprüfung)")
        return rec

    async def plan_load(self, plan_id: int) -> dict:
        rec = self.plans.get(int(plan_id))
        if not rec or not rec["machine"]:
            return {"ok": False, "text": f"Plan {plan_id} nicht vorhanden oder fehlerhaft"}
        try:
            await asyncio.to_thread(self.link.transfer_plan, rec["machine"])
        except NotImplementedError as e:
            self.log("error", "Plan", str(e))
            return {"ok": False, "text": str(e)}
        r = await self.command("PlanLoad", value=int(plan_id), source="Plan")
        if r["ok"]:
            m = rec["machine"]["material"]
            await self.apply_param({"plan_id": plan_id, "diameter_mm": m["diameter_mm"], "wall_mm": m["wall_mm"],
                                    "yield_mpa": m["yield_mpa"], "force_limit_n": m["force_limit_n"],
                                    **{k: self.status_json.get("param", {}).get(k, 100)
                                       for k in ("feed_override_pct", "single_axis_velo_pct")}})
        return r

    async def step_run(self, elements: list[dict], process: dict, material: dict) -> dict:
        """Schrittbetrieb: Element(e) ab aktueller Z1-Position planen, laden und starten."""
        try:
            geo = GeoPlan(name="Schritt", material=Material(**{k: float(v) for k, v in material.items()}),
                          process=Process(**{k: float(v) for k, v in process.items() if k != "z1_start_mm"}),
                          elements=[element_from_json(e, i) for i, e in enumerate(elements)])
        except (PlanError, TypeError) as e:
            return {"ok": False, "text": str(e)}
        rec = self.plan_from_geo(geo, name=f"Schritt: {', '.join(e['type'] for e in elements)}")
        if not rec["machine"]:
            return {"ok": False, "text": "Schritt nicht ausführbar – siehe Meldungen", "plan": rec}
        for c, kw in (("plan", {}), ("MaterialConfirm", {"value": rec["plan_id"]}), ("SeqStart", {})):
            r = await (self.plan_load(rec["plan_id"]) if c == "plan" else self.command(c, source="Schritt", **kw))
            if not r["ok"]:
                return {**r, "plan": rec}
        return {"ok": True, "text": f"Schritt mit Plan {rec['plan_id']} gestartet", "plan": rec}

    # ── Debug-Konsole ───────────────────────────────────────────────────
    async def debug(self, text: str) -> dict:
        parts = text.strip().split()
        if not parts:
            return {"ok": True, "text": ""}
        c, args = parts[0].lower(), parts[1:]
        kv = dict(a.split("=", 1) for a in args if "=" in a)
        if c in ("help", "?", "hilfe"):
            return {"ok": True, "text": (
                "Befehle:\n"
                "  status                     Kurzstatus\n"
                "  alarms                     aktive Alarme\n"
                "  cmd <Kommando> [axis=Z1 pos=10 vel=5 clamp=1 value=0]\n"
                "                             beliebiges E_HmiCmd senden, z. B. cmd AxisEnable axis=X\n"
                "  ack                        alle Alarme quittieren\n"
                "  plans | plan <id>          Pläne anzeigen\n"
                "  fault <n>                  Simulation: 1 Achsfehler Z1, 2 X, 3 A, 4 Druckabfall, 5 Not-Halt, 6 Schleppfehler\n"
                "  input <n> <0|1>            Simulation: 1 Druckschalter, 2 Not-Halt\n"
                "  cmds                       Liste aller Kommandos")}
        if c == "status":
            s = self.status_json
            if not s:
                return {"ok": False, "text": "noch kein Status"}
            ax = " ".join(f"{n}={v['pos']:.2f}({v['state'][:4]})" for n, v in s["axes"].items())
            return {"ok": True, "text": f"Betriebsart {s['mode']}, Kinematik {s['kin']['state']}, G2 {s['step_g2_text']}, "
                                        f"HMI lebt: {s['hmi_alive']}\n{ax}\nGrundstellung fehlt: "
                                        f"{', '.join(s['base_missing']) or '–'}"}
        if c == "alarms":
            lst = self.alarms_json.get("list", [])
            return {"ok": True, "text": "\n".join(f"#{a['slot']} {a['severity']} {a['text']} {a['source']} "
                                                  f"{'aktiv' if a['active'] else 'behoben'}"
                                                  f"{'' if a['acked'] else ', nicht quittiert'}" for a in lst) or "keine Alarme"}
        if c == "cmds":
            return {"ok": True, "text": ", ".join(f"{e.name}={int(e)}" for e in E_HmiCmd)}
        if c == "ack":
            return await self.command("AlarmAck", value=0, source="Konsole")
        if c == "cmd" and args:
            try:
                return await self.command(args[0], axis=kv.get("axis", 0), clamp=int(kv.get("clamp", 0)),
                                          position=float(kv.get("pos", 0)), velocity=float(kv.get("vel", 0)),
                                          value=int(kv.get("value", 0)), source="Konsole")
            except ValueError as e:
                return {"ok": False, "text": str(e)}
        if c == "fault" and args:
            return await self.command("SimInjectFault", value=int(args[0]), source="Konsole")
        if c == "input" and len(args) >= 2:
            return await self.command("SimSetInput", value=int(args[0]), position=float(args[1]), source="Konsole")
        if c == "plans":
            return {"ok": True, "text": "\n".join(f"{p['plan_id']}: {p['name']} ({p['kind']}, "
                                                  f"{'ok' if p['report'].get('ok', True) else 'Fehler'})"
                                                  for p in self.plans.values()) or "keine Pläne"}
        if c == "plan" and args:
            p = self.plans.get(int(args[0]))
            if not p:
                return {"ok": False, "text": "unbekannt"}
            m = p["machine"]
            if not m:
                return {"ok": False, "text": "Plan fehlerhaft"}
            lines = [f"Plan {p['plan_id']} {p['name']}: {len(m['bends'])} Abschnitte"]
            for i, b in enumerate(m["bends"], 1):
                lines.append(f"  {i}: Z1 {b['z1_start_mm']}…{b['z1_fin_mm']} mm, Vorschub {b.get('feed_mm_s')} mm/s, "
                             f"{len(b['points'])} Punkte, Rotationen {b.get('rotations')}, NH {b.get('clamps')}")
            return {"ok": True, "text": "\n".join(lines)}
        return {"ok": False, "text": f"Unbekannter Befehl „{parts[0]}“ – „help“ zeigt alle Befehle"}
