"""Fixtures compartilhadas para os testes dos giro packs."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.loader import enrich  # noqa: E402
from tests.helpers import make_raw_row  # noqa: E402


@pytest.fixture
def as_of() -> pd.Timestamp:
    return pd.Timestamp("2026-08-01 12:00:00")


@pytest.fixture
def enriched_df(as_of) -> pd.DataFrame:
    """DataFrame enriquecido com cenários cobrindo malha, SOP, SLA e alertas."""
    rows = [
        make_raw_row(
            waybill="AJ-OK",
            status="Pedido entregue",
            delay_days=0.0,
            signed_at="2026-07-08 10:00:00",
        ),
        make_raw_row(
            waybill="AJ-MESH-ENTRY",
            pre_point="RS-W-D033",
            entry_point="RS-W-D064",
            delivery_point="RS-W-D064",
            delay_days=3.0,
        ),
        make_raw_row(
            waybill="AJ-MESH-DELIV",
            pre_point="RS-W-D033",
            entry_point="RS-W-D033",
            delivery_point="RS-W-D105",
            delay_days=2.0,
        ),
        make_raw_row(
            waybill="AJ-SOP",
            status="Devolvido pelo ponto",
            attempt_1="2026-07-05 09:00:00",
            attempt_2="--",
            attempt_3="--",
            delay_days=10.0,
        ),
        make_raw_row(
            waybill="AJ-ESTOURADO",
            delay_days=50.0,
            closed_loop_days=45.0,
            hub1_op_h=40.0,
            hub1_transit_h=10.0,
            base_dispatch_h=2.0,
            dispatch_transit_h=1.0,
            base_sign_h=1.0,
        ),
        make_raw_row(
            waybill="AJ-D1",
            delay_days=44.0,
            closed_loop_days=45.0,
        ),
        make_raw_row(
            waybill="AJ-ALTO",
            delay_days=42.0,
            closed_loop_days=90.0,
            hub1_op_h=5.0,
            base_dispatch_h=50.0,
            dispatch_transit_h=10.0,
            base_sign_h=5.0,
        ),
        make_raw_row(
            waybill="AJ-HUB",
            delay_days=8.0,
            closed_loop_days=60.0,
            hub1_op_h=80.0,
            hub1_transit_h=10.0,
            base_dispatch_h=2.0,
            dispatch_transit_h=1.0,
            base_sign_h=1.0,
        ),
    ]
    return enrich(pd.DataFrame(rows), as_of=as_of)
