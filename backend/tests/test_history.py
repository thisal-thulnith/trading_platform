import io
import zipfile

import pandas as pd

from app.quant.lab import history as H


def _zip(text: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("x.csv", text)
    return b.getvalue()


def test_klines_handle_microsecond_timestamps():
    ms = "1735689600000,1,2,0.5,1.5,10,1735693199999,15,7,5,7.5,0\n"
    us = "1735693200000000,1.5,2,1,1.8,10,1735696799999999,18,9,5,9,0\n"
    d = H.parse_klines(_zip(ms + us))
    assert list(d["time"]) == [1735689600000, 1735693200000]


def test_metrics_aggregate_to_hours():
    rows = ["create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
            "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio"]
    rows += [f"2025-01-15 00:{m:02d}:00,X,{100 + m},{1000 + m},2,1.5,1.2,{1 + m / 100}" for m in range(0, 60, 5)]
    rows += ["2025-01-15 01:00:00,X,200,2000,2,1.5,1.2,1"]
    d = H.parse_metrics(_zip("\n".join(rows)))
    assert len(d) == 2 and d["oi"].iloc[0] == 155 and d["time"].iloc[1] - d["time"].iloc[0] == 3_600_000
    assert d["time"].iloc[0] == int(pd.Timestamp("2025-01-15", tz="UTC").timestamp() * 1000)


def test_depth_bands_and_imbalance():
    rows = ["timestamp,percentage,depth,notional"]
    for pct, n in ((-1, 300), (1, 100), (-5, 500), (5, 500)):
        rows.append(f"2025-01-15 00:00:05,{pct},1,{n}")
    d = H.parse_depth(_zip("\n".join(rows)))
    assert d["imb1"].iloc[0] == 0.5 and d["imb5"].iloc[0] == 0.0 and "nm1" in d.columns
