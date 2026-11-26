"""Enums in hmi_types.py müssen exakt den Enums im TwinCAT-Projekt (01_DUT) entsprechen."""
import re
from pathlib import Path

import pytest

from biegbert.plc import hmi_types

DUT = Path(__file__).resolve().parents[2] / "plc" / "Biegbert" / "Biegbert" / "Biegbert_SPS" / "01_DUT"
NAMES = ["E_Axis", "E_AxisState", "E_ClampState", "E_CmdResult", "E_HmiCmd", "E_KinState", "E_MotionMode",
         "E_OperatingMode", "E_AlarmSeverity", "E_AlarmCode"]


@pytest.mark.skipif(not DUT.is_dir(), reason="SPS-Projekt nicht im Repository")
@pytest.mark.parametrize("name", NAMES)
def test_enum_matches_tcdut(name):
    text = (DUT / f"{name}.TcDUT").read_text(encoding="utf-8-sig")
    plc = {m.group(1): int(m.group(2)) for m in re.finditer(r"^\s*(\w+)\s*:=\s*(\d+)", text, re.M)}
    py = {e.name.rstrip("_"): int(e) for e in getattr(hmi_types, name)}
    assert py == plc
