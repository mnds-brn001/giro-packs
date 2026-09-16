"""Ingestão do monitoramento e localização do Parquet da torre."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.columns import COL
from src.ingest import ingest_monitoramento, resolve_monitoramento, stamp_from_export
from src.loader import default_data_path
from tests.helpers import make_raw_row


def _excel_frame() -> pd.DataFrame:
    row = make_raw_row()
    return pd.DataFrame([{COL[k]: v for k, v in row.items() if k in COL}])


def test_stamp_from_export_filename():
    path = Path("monitoramento da pontualidade de pedido-20260908104227066-MVgO7S.xlsx")
    assert stamp_from_export(path) == "2026-09-08"


def test_stamp_from_export_fallback():
    assert stamp_from_export(Path("monitoramento.xlsx"), fallback=date(2026, 1, 2)) == "2026-01-02"


def test_resolve_monitoramento_prefers_drop_zone(tmp_path):
    mon = tmp_path / "data" / "monitoramento"
    mon.mkdir(parents=True)
    drop = mon / "monitoramento-drop.xlsx"
    root = tmp_path / "monitoramento-root.xlsx"
    drop.write_bytes(b"PK")
    root.write_bytes(b"PK")
    found = resolve_monitoramento(root=tmp_path)
    assert found == drop.resolve()


def test_ingest_writes_dated_and_latest_parquet(tmp_path):
    mon = tmp_path / "data" / "monitoramento"
    mon.mkdir(parents=True)
    xlsx = mon / "monitoramento da pontualidade de pedido-20260908104227066-xx.xlsx"
    _excel_frame().to_excel(xlsx, index=False)

    result = ingest_monitoramento(xlsx, root=tmp_path)
    dated = tmp_path / "data" / "parquet" / "monitoramento_2026-09-08.parquet"
    latest = tmp_path / "data" / "parquet" / "monitoramento_latest.parquet"
    assert result.stamp == "2026-09-08"
    assert result.skipped is False
    assert dated.is_file()
    assert latest.is_file()
    assert result.parquet == dated
    assert result.latest == latest
    loaded = pd.read_parquet(latest)
    assert loaded.iloc[0][COL["waybill"]] == "AJ26000001"

    from src.backlog_lastmile import load_lastmile_source

    lm = load_lastmile_source(result.parquet)
    assert lm.iloc[0]["waybill"] == "AJ26000001"

    again = ingest_monitoramento(xlsx, root=tmp_path)
    assert again.skipped is True


def test_default_data_path_prefers_latest_pointer(tmp_path):
    parquet_dir = tmp_path / "data" / "parquet"
    parquet_dir.mkdir(parents=True)
    (tmp_path / "monitoramento-old.parquet").write_bytes(b"PAR1")
    latest = parquet_dir / "monitoramento_latest.parquet"
    latest.write_bytes(b"PAR1")
    found = default_data_path(tmp_path)
    assert found == latest


def test_default_data_path_newest_xlsx_in_drop_zone(tmp_path):
    mon = tmp_path / "data" / "monitoramento"
    mon.mkdir(parents=True)
    older = mon / "monitoramento-a.xlsx"
    newer = mon / "monitoramento-b.xlsx"
    older.write_bytes(b"PK")
    newer.write_bytes(b"PK")
    older_stat = older.stat()
    import os
    os.utime(older, (older_stat.st_atime, older_stat.st_mtime - 120))
    found = default_data_path(tmp_path)
    assert found == newer
