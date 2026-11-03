from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.status import data_status, robot_status

NY = ZoneInfo("America/New_York")


class P:
    def __init__(self, last=None, fail=False):
        self.last, self.fail = last, fail

    def history(self, *a):
        if self.fail:
            raise ConnectionError("nät")
        if self.last is None:
            return None
        return pd.DataFrame({"Close": [500.0]}, index=[pd.Timestamp(self.last)])


def test_data_status_colors():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=NY)  # börsen öppen
    assert data_status(P("2026-11-03 11:50"), now)[0] == "grön"
    assert data_status(P("2026-11-03 11:00"), now)[0] == "gul"
    assert data_status(P(fail=True), now)[0] == "röd"
    assert data_status(P(None), now)[0] == "röd"
    assert data_status(P("2026-11-02 15:55"), datetime(2026, 11, 3, 8, 0, tzinfo=NY))[0] == "grön"  # stängd


def test_robot_status_colors():
    now = datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc)  # 12:00 New York, öppet
    assert robot_status({"last_run": "2026-11-03T16:57:00+00:00"}, now)[0] == "grön"
    assert robot_status({"last_run": "2026-11-03T16:40:00+00:00"}, now)[0] == "gul"
    assert robot_status({"last_run": "2026-11-03T15:00:00+00:00"}, now)[0] == "röd"
    assert robot_status({}, now)[0] == "gul"
    assert robot_status({"last_run": "2026-11-02T20:00:00+00:00"},
                        datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc))[0] == "grön"  # natt
