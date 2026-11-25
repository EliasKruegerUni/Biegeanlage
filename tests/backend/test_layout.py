"""Byte-Layout der HMI-Strukturen (pack_mode 1). Größen gegen die SPS-Typen aus 01_DUT."""
from biegbert.plc.layout import (SIZE_ALARMS, SIZE_AXIS, SIZE_STATUS, pack_alarms, pack_status, unpack_alarms,
                                 unpack_status)
from biegbert.plc.hmi_types import ST_AlarmEntry, ST_HmiAlarms, ST_HmiStatus


def test_sizes():
    # ST_HmiAxisStatus: INT + 3 BOOL + UDINT + 6 LREAL = 2 + 3 + 4 + 48
    assert SIZE_AXIS == 57
    assert SIZE_STATUS == 495
    # ST_HmiAlarms: UDINT + UINT + BOOL + INT + 32 × (UDINT + INT + DINT + UDINT + ULINT + 2 BOOL)
    assert SIZE_ALARMS == 9 + 32 * 24


def test_roundtrip_status():
    st = ST_HmiStatus(nLifeCounter=123, bHmiAlive=True, eMode=12, dwBaseStateMissing=0b1010)
    st.aAxis[2].fActPos = 7.25
    st.aClamp[5].eState = 3
    st.stIo.abPanelIn[3] = True
    st.stParamActive.stMaterial.fDiameter_mm = 15.0
    assert unpack_status(pack_status(st)) == st


def test_roundtrip_alarms():
    al = ST_HmiAlarms(nChangeCounter=5, nCount=1, eHighestSeverity=4)
    al.aEntry[0] = ST_AlarmEntry(2000, 4, 3, 0x4260, 133_000_000_000_000_000, True, False)
    assert unpack_alarms(pack_alarms(al)) == al
