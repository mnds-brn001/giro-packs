"""Corte D+1: o que sai de vence hoje e o que entra."""

from __future__ import annotations

import pandas as pd

from src.corte_8h import build_corte_report


def test_d0_after_8h_leaves_today_and_yesterday_enters_today():
    as_of = pd.Timestamp("2026-10-22")
    tracking = pd.DataFrame(
        {
            "waybill": ["HOJE", "ONTEM", "CEDO", "D1"],
            "status": ["Em rota de entrega"] * 4,
            "last_point": ["RS-W-D076"] * 4,
            "dest_state": ["RS"] * 4,
            "dest_city": ["Canoas", "Canoas", "Canoas", "Novo Hamburgo"],
            "base_inbound": [
                "2026-10-22 10:00:00",
                "2026-10-21 10:00:00",
                "2026-10-22 07:59:00",
                "2026-10-22 10:00:00",
            ],
        }
    )
    lookup = pd.DataFrame(
        {
            "dest_state": ["RS", "RS"],
            "dest_city_n": ["canoas", "novo hamburgo"],
            "base_n": ["RS-W-D076", "RS-W-D076"],
            "lm_dias": [0, 1],
        }
    )
    resp = pd.DataFrame({"base": ["RS-W-D076"], "responsavel": ["Bruno Mendes"]})
    report = build_corte_report(tracking, lookup, resp, as_of)

    assert report.corte == 2
    assert report.chegou_hoje == 1
    assert report.chegou_ontem == 1
    assert report.d0_cedo == 1
    assert report.donos[0]["nome"] == "Bruno Mendes"
