"""Carregamento e normalização do export de tracking."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import numpy as np
import pandas as pd

from .columns import COL, DATETIME_KEYS, NUMERIC_KEYS
from .validate import ValidationReport, validate_tracking_data

EMPTY_MARKERS = {"", "--", "—", "nan", "None", "NaT", "null", "NULL"}


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

    # Merchant simplificado
    if "merchant" in out.columns:
        m = out["merchant"].astype(str)
        out["merchant_group"] = np.select(
            [
                m.str.contains("SHEIN", case=False, na=False),
                m.str.contains("SHOPEE", case=False, na=False),
                m.str.contains("TIKTOK|TEMU|temu", case=False, na=False),
            ],
            ["SHEIN", "SHOPEE", "TEMU/CROSS"],
            default=np.where(out["merchant"].isna() | (m.isin(EMPTY_MARKERS)), "SEM MERCHANT", "OUTROS"),
        )
    else:
        out["merchant_group"] = "SEM MERCHANT"

    return out


def load_tracking(
    source: str | Path | BinaryIO,
    as_of: pd.Timestamp | None = None,
    *,
    validate: bool = True,
) -> pd.DataFrame:
    """Lê Excel/CSV da tabela mestra e devolve DataFrame enriquecido.

    Com ``validate=True`` (padrão), roda checagens de schema/qualidade
    *antes* do enrich e anexa ``df.attrs["validation"]`` com o relatório.
    """
    if hasattr(source, "read"):
        df = pd.read_excel(source)
    else:
        path = Path(source)
        if path.suffix.lower() in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        elif path.suffix.lower() == ".csv":
            df = pd.read_csv(path)
        else:
            raise ValueError(f"Formato não suportado: {path.suffix}")

    canonical = rename_to_canonical(df)
    report: ValidationReport | None = None
    if validate:
        # Datas/nulos precisam ser avaliados antes do coerce do enrich
        report = validate_tracking_data(canonical)
    enriched = enrich(canonical, as_of=as_of)
    if report is not None:
        # dict JSON-serializável — objeto ValidationReport quebra st.dataframe/Arrow
        enriched.attrs["validation"] = report.to_dict()
    return enriched


def default_data_path(base_dir: Path | None = None) -> Path | None:
    """Localiza o XLSX padrão na pasta do projeto (ou em data/)."""
    root = base_dir or Path(__file__).resolve().parent.parent
    candidates: list[Path] = []
    for pattern in ("monitoramento*.xlsx", "data/demo_tracking.xlsx", "data/*.xlsx"):
        candidates.extend(root.glob(pattern))
    matches = sorted({m.resolve() for m in candidates if not m.name.startswith("~$")})
    return matches[0] if matches else None
