"""Carregamento e normalização do export de tracking."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import numpy as np
import pandas as pd

from .columns import COL, DATETIME_KEYS, NUMERIC_KEYS
from .validate import ValidationReport, validate_tracking_data

EMPTY_MARKERS = {"", "--", "—", "nan", "None", "NaT", "null", "NULL"}

PARQUET_SUFFIXES = {".parquet", ".pq"}
EXCEL_SUFFIXES = {".xlsx", ".xls"}
CSV_SUFFIXES = {".csv"}


def _is_filled(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    return series.notna() & ~s.isin(EMPTY_MARKERS)


def _to_numeric(series: pd.Series) -> pd.Series:
    cleaned = series.replace(list(EMPTY_MARKERS), np.nan)
    return pd.to_numeric(cleaned, errors="coerce")


def _to_datetime(series: pd.Series) -> pd.Series:
    cleaned = series.replace(list(EMPTY_MARKERS), np.nan)
    return pd.to_datetime(cleaned, errors="coerce")


def rename_to_canonical(df: pd.DataFrame) -> pd.DataFrame:
    """Renomeia colunas Excel → chaves canônicas; mantém extras se existirem."""
    reverse = {v: k for k, v in COL.items()}
    out = df.rename(columns=reverse)
    return out


def enrich(df: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Deriva flags e métricas usadas pelas rotinas de giro."""
    as_of = as_of or pd.Timestamp.now()
    out = df.copy()

    for key in DATETIME_KEYS:
        if key in out.columns:
            out[key] = _to_datetime(out[key])

    for key in NUMERIC_KEYS:
        if key in out.columns:
            out[key] = _to_numeric(out[key])

    # Malha: teórico vs real (entrega) e vs entrada física
    pre = out.get("pre_point")
    real = out.get("delivery_point")
    entry = out.get("entry_point")

    if pre is not None and entry is not None:
        out["mesh_vs_entry"] = np.where(
            _is_filled(pre) & _is_filled(entry) & (pre.astype(str).str.strip() != entry.astype(str).str.strip()),
            "MISMATCH",
            np.where(_is_filled(pre) & _is_filled(entry), "OK", "N/A"),
        )
    else:
        out["mesh_vs_entry"] = "N/A"

    if pre is not None and real is not None:
        out["mesh_vs_delivery"] = np.where(
            _is_filled(pre) & _is_filled(real) & (pre.astype(str).str.strip() != real.astype(str).str.strip()),
            "MISMATCH",
            np.where(_is_filled(pre) & _is_filled(real), "OK", "N/A"),
        )
    else:
        out["mesh_vs_delivery"] = "N/A"

    out["mesh_mismatch"] = (out["mesh_vs_entry"] == "MISMATCH") | (out["mesh_vs_delivery"] == "MISMATCH")

    # Tentativas last-mile
    for k in ("attempt_1", "attempt_2", "attempt_3"):
        if k in out.columns:
            out[f"has_{k}"] = _is_filled(out[k])
        else:
            out[f"has_{k}"] = False

    out["attempts_filled"] = (
        out["has_attempt_1"].astype(int)
        + out["has_attempt_2"].astype(int)
        + out["has_attempt_3"].astype(int)
    )

    # SOP: devolução sem 3 tentativas
    status = out.get("status", pd.Series(dtype=str)).astype(str)
    out["is_returned"] = status.str.contains("Devolvido", case=False, na=False)
    out["sop_violation"] = out["is_returned"] & (out["attempts_filled"] < 3)

    # Dias até deadline do ciclo fechado
    if "closed_loop_at" in out.columns:
        out["days_to_closed_loop"] = (out["closed_loop_at"] - as_of).dt.total_seconds() / 86400.0
    else:
        out["days_to_closed_loop"] = np.nan

    # Risco financeiro por proximidade do ciclo / atraso
    delay = out.get("delay_days", pd.Series(np.nan, index=out.index))
    loop_days = out.get("closed_loop_days", pd.Series(np.nan, index=out.index))
    remaining = loop_days - delay
    out["loop_remaining_days"] = remaining

    delivered = status.str.contains("entregue", case=False, na=False) & ~out["is_returned"]
    out["is_delivered"] = delivered
    out["is_open"] = ~delivered

    conditions = [
        out["is_open"] & remaining.notna() & (remaining <= 0),
        out["is_open"] & remaining.notna() & (remaining <= 1),
        out["is_open"] & remaining.notna() & (remaining <= 5),
        out["is_open"] & delay.notna() & (delay >= 40),
        out["is_open"] & delay.notna() & (delay >= 20),
    ]
    choices = ["ESTOURADO", "D-1 CRÍTICO", "D-5 ALERTA", "ALTO RISCO", "RISCO MÉDIO"]
    out["risk_tier"] = np.select(conditions, choices, default=np.where(out["is_open"], "MONITORAR", "OK"))

    # Matriz HUB vs Base — fatia do tempo operacional
    hub_parts = []
    for k in ("hub1_op_h", "hub2_op_h", "hub3_op_h", "hub1_transit_h", "hub2_transit_h", "hub3_transit_h"):
        if k in out.columns:
            hub_parts.append(out[k].fillna(0).clip(lower=0))
    base_parts = []
    for k in ("base_dispatch_h", "dispatch_transit_h", "base_sign_h"):
        if k in out.columns:
            base_parts.append(out[k].fillna(0).clip(lower=0))

    out["hub_hours"] = sum(hub_parts) if hub_parts else 0.0
    out["base_hours"] = sum(base_parts) if base_parts else 0.0
    total = out["hub_hours"] + out["base_hours"]
    out["hub_share_pct"] = np.where(total > 0, out["hub_hours"] / total * 100, np.nan)
    out["base_share_pct"] = np.where(total > 0, out["base_hours"] / total * 100, np.nan)
    out["delay_owner"] = np.where(
        total <= 0,
        "INDEFINIDO",
        np.where(
            out["hub_share_pct"] >= 80,
            "HUB (transbordo)",
            np.where(out["base_share_pct"] >= 80, "BASE (last mile)", "COMPARTILHADO"),
        ),
    )

    # Merchant: vazio → nome do cliente (proxy operacional do dia a dia)
    if "merchant" in out.columns:
        blank_merchant = ~_is_filled(out["merchant"])
        if "client" in out.columns:
            client_fill = out["client"].where(_is_filled(out["client"]))
            out["merchant"] = out["merchant"].where(~blank_merchant, client_fill)
        m = out["merchant"].astype(str)
        still_blank = ~_is_filled(out["merchant"])
        proxied = blank_merchant & ~still_blank
        out["merchant_group"] = np.select(
            [
                m.str.contains("SHEIN", case=False, na=False),
                m.str.contains("SHOPEE", case=False, na=False),
                m.str.contains("TIKTOK|TEMU|temu", case=False, na=False),
                proxied,
            ],
            ["SHEIN", "SHOPEE", "TEMU/CROSS", m.str.strip()],
            default=np.where(still_blank, "SEM MERCHANT", "OUTROS"),
        )
    elif "client" in out.columns:
        out["merchant"] = out["client"].where(_is_filled(out["client"]))
        out["merchant_group"] = np.where(_is_filled(out["merchant"]), out["merchant"].astype(str).str.strip(), "SEM MERCHANT")
    else:
        out["merchant_group"] = "SEM MERCHANT"

    return out


def _suffix_format(name: str | None) -> str | None:
    if not name:
        return None
    suffix = Path(str(name)).suffix.lower()
    if suffix in PARQUET_SUFFIXES:
        return "parquet"
    if suffix in EXCEL_SUFFIXES:
        return "excel"
    if suffix in CSV_SUFFIXES:
        return "csv"
    return None


def _magic_format(header: bytes) -> str | None:
    if header.startswith(b"PAR1"):
        return "parquet"
    if header.startswith(b"PK") or header.startswith(b"\xd0\xcf\x11\xe0"):
        return "excel"
    return None


def _detect_format(source: str | Path | BinaryIO, filename: str | None = None) -> str:
    name = filename or (str(source) if isinstance(source, (str, Path)) else getattr(source, "name", None))
    fmt = _suffix_format(name)
    if fmt:
        return fmt
    if hasattr(source, "read"):
        pos = source.tell() if hasattr(source, "tell") else 0
        header = source.read(8)
        if hasattr(source, "seek"):
            source.seek(pos)
        sniffed = _magic_format(header if isinstance(header, (bytes, bytearray)) else b"")
        if sniffed:
            return sniffed
    raise ValueError(f"Formato não suportado: {name or type(source).__name__}")


def _read_excel(source: str | Path | BinaryIO) -> pd.DataFrame:
    try:
        return pd.read_excel(source, engine="calamine")
    except (ValueError, ImportError, Exception):
        if hasattr(source, "seek"):
            source.seek(0)
        return pd.read_excel(source)


def read_tracking_table(
    source: str | Path | BinaryIO,
    *,
    filename: str | None = None,
) -> pd.DataFrame:
    """Lê Excel, CSV ou Parquet cru (colunas originais da Tabela Mestra)."""
    fmt = _detect_format(source, filename=filename)
    if fmt == "parquet":
        return pd.read_parquet(source)
    if fmt == "csv":
        return pd.read_csv(source)
    return _read_excel(source)


def _stringify_mixed_object_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Parquet recusa colunas object com tipos mistos (str + número + datetime)."""
    out = df.copy()
    for col in out.columns:
        series = out[col]
        if series.dtype != object:
            continue
        types = {type(v) for v in series.dropna()}
        if len(types) > 1:
            out[col] = series.map(lambda v: v if pd.isna(v) else str(v))
    return out


def write_tracking_parquet(df: pd.DataFrame, destination: str | Path) -> Path:
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    prepared = _stringify_mixed_object_columns(df)
    prepared.to_parquet(dest, engine="pyarrow", index=False, compression="zstd")
    return dest


def convert_tracking_to_parquet(
    source: str | Path,
    destination: str | Path | None = None,
) -> Path:
    """Converte Tabela Mestra Excel/CSV → Parquet, preservando nomes de coluna."""
    src = Path(source)
    dest = Path(destination) if destination else src.with_suffix(".parquet")
    raw = read_tracking_table(src)
    return write_tracking_parquet(raw, dest)


def load_tracking(
    source: str | Path | BinaryIO,
    as_of: pd.Timestamp | None = None,
    *,
    validate: bool = True,
    filename: str | None = None,
) -> pd.DataFrame:
    """Lê Excel/CSV/Parquet da tabela mestra e devolve DataFrame enriquecido.

    Com ``validate=True`` (padrão), roda checagens de schema/qualidade
    *antes* do enrich e anexa ``df.attrs["validation"]`` com o relatório.
    """
    df = read_tracking_table(source, filename=filename)

    canonical = rename_to_canonical(df)
    report: ValidationReport | None = None
    if validate:
        report = validate_tracking_data(canonical)
    enriched = enrich(canonical, as_of=as_of)
    if report is not None:
        enriched.attrs["validation"] = report.to_dict()
    return enriched


def _newest(files: list[Path]) -> Path | None:
    existing = [p for p in files if p.is_file() and not p.name.startswith("~$")]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def default_data_path(base_dir: Path | None = None) -> Path | None:
    """Localiza Parquet/XLSX padrão para os packs (auto-load).

    Ordem: ``data/parquet/monitoramento_latest.parquet``, Parquet datado,
    export em ``data/monitoramento/``, legado na raiz, demo em ``data/``.
    """
    root = base_dir or Path(__file__).resolve().parent.parent
    parquet_dir = root / "data" / "parquet"
    export_dir = root / "data" / "monitoramento"

    latest = parquet_dir / "monitoramento_latest.parquet"
    if latest.is_file():
        return latest

    found = _newest(
        [p for p in parquet_dir.glob("monitoramento_*.parquet") if p.name != "monitoramento_latest.parquet"]
    )
    if found:
        return found
    found = _newest(list(export_dir.glob("monitoramento*.xlsx")) + list(export_dir.glob("monitoramento*.xls")))
    if found:
        return found
    found = _newest(list(root.glob("monitoramento*.parquet")))
    if found:
        return found
    found = _newest(list(root.glob("monitoramento*.xlsx")) + list(root.glob("monitoramento*.xls")))
    if found:
        return found

    demo_xlsx = [
        root / "data" / "demo_tracking.xlsx",
        *list((root / "data").glob("*.xlsx")),
    ]
    return _newest(demo_xlsx)
