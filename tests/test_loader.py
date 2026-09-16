"""Leitura Excel/Parquet e localização do arquivo padrão."""

from __future__ import annotations

import io

import pandas as pd

from src.columns import COL
from src.loader import convert_tracking_to_parquet, default_data_path, load_tracking
from src.validate import get_validation_report
from tests.helpers import make_raw_row


def _excel_frame() -> pd.DataFrame:
    row = make_raw_row()
    return pd.DataFrame([{COL[k]: v for k, v in row.items() if k in COL}])


def test_load_tracking_parquet_roundtrip(tmp_path):
    xlsx = tmp_path / "sample.xlsx"
    parquet = tmp_path / "sample.parquet"
    _excel_frame().to_excel(xlsx, index=False)

    written = convert_tracking_to_parquet(xlsx, parquet)
    assert written == parquet
    assert parquet.exists()

    loaded = load_tracking(parquet)
    assert loaded.iloc[0]["waybill"] == "AJ26000001"
    report = get_validation_report(loaded)
    assert report is not None
    assert report.ok


def test_load_tracking_parquet_bytes():
    frame = _excel_frame()
    buf = io.BytesIO()
    frame.to_parquet(buf, engine="pyarrow", index=False)
    loaded = load_tracking(io.BytesIO(buf.getvalue()), filename="tabela.parquet")
    assert loaded.iloc[0]["waybill"] == "AJ26000001"


def test_load_tracking_parquet_magic_bytes():
    frame = _excel_frame()
    buf = io.BytesIO()
    frame.to_parquet(buf, engine="pyarrow", index=False)
    loaded = load_tracking(io.BytesIO(buf.getvalue()))
    assert loaded.iloc[0]["status"] == "Em rota de entrega"


def test_default_data_path_prefers_parquet(tmp_path):
    (tmp_path / "monitoramento-demo.xlsx").write_bytes(b"PK")
    (tmp_path / "monitoramento-demo.parquet").write_bytes(b"PAR1")
    found = default_data_path(tmp_path)
    assert found is not None
    assert found.suffix == ".parquet"


def test_default_data_path_xlsx_fallback(tmp_path):
    (tmp_path / "monitoramento-demo.xlsx").write_bytes(b"PK")
    found = default_data_path(tmp_path)
    assert found is not None
    assert found.suffix == ".xlsx"
