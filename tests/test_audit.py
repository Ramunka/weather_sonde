from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.archive.audit_telemetry import audit_rows

T0 = datetime(2025, 8, 1, 12, 0, tzinfo=timezone.utc)


def row(sec, **kw):
    values = dict(humidity=50.0, temperature=10.0, pressure=900, signal_strength=-70)
    values.update(kw)
    return SimpleNamespace(ts=T0 + timedelta(seconds=sec), **values)


def test_empty():
    assert "error" in audit_rows([])


def test_gaps_and_outliers():
    result = audit_rows([row(0), row(2), row(20, humidity=104.0), row(22, signal_strength=-140)])
    assert result["total_points"] == 4
    assert result["gaps"] == [{"start": (T0 + timedelta(seconds=2)).isoformat(),
                               "end": (T0 + timedelta(seconds=20)).isoformat(), "duration": 18}]
    assert [(o["field"], o["value"]) for o in result["outliers"]] == [("humidity", 104.0), ("signal_strength", -140)]
