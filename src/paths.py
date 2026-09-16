"""Pastas canônicas de ingestão da Tabela Mestra."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MONITORAMENTO_DIR = ROOT / "data" / "monitoramento"
PARQUET_DIR = ROOT / "data" / "parquet"
PRAZO_DIR = ROOT / "data" / "prazo"
ENTREGUES_DIR = ROOT / "data" / "entregues"

EXPORT_GLOB = "monitoramento*.xlsx"
PRAZO_GLOB = "*prazo*.xlsx"
