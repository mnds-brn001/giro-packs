"""Cobrança TikTok: linha sem cidade repetida e ciclo do cliente no dia."""

from __future__ import annotations

import pandas as pd

from src.preventivo_lastmile import RESUMO_FORA, RESUMO_VENCE_HOJE
from src.tiktok_sla import tiktok_ciclo_message, tiktok_message


def test_route_line_does_not_repeat_destination_city():
    as_of = pd.Timestamp("2026-10-22")
    group = pd.DataFrame(
        {
            "resumo": [RESUMO_VENCE_HOJE, RESUMO_FORA],
            "status": ["Em rota de entrega", "Em rota de entrega"],
            "waybill": ["AJ-HOJE", "AJ-FORA"],
            "dest_city": ["Palmeira", "Carambeí"],
            "courier": ["wuilmanpalmeirapr050", "alexandrepr050"],
            "dias_fora": [0, 15],
        }
    )
    message = tiktok_message("PR-W-D050", group, as_of)

    assert "AJ-HOJE\tPalmeira\twuilmanpalmeirapr050" in message.splitlines()
    assert "AJ-HOJE\tPalmeira\twuilmanpalmeirapr050\tPalmeira" not in message
    assert "AJ-FORA\tCarambeí\talexandrepr050\t15d fora" in message


def test_ciclo_message_keeps_only_deadlines_on_the_reference_day():
    as_of = pd.Timestamp("2026-10-22")
    group = pd.DataFrame(
        {
            "status": ["Em rota de entrega", "Recebido no ponto de entrega"],
            "waybill": ["AJ-HOJE", "AJ-ONTEM"],
            "dest_city": ["Ponta Grossa", "Castro"],
            "courier": ["igorpr050", ""],
            "closed_loop_at": ["2026-10-22 23:59:59", "2026-10-21 23:59:59"],
        }
    )
    message = tiktok_ciclo_message("PR-W-D050", group, as_of)

    assert "Fecha o ciclo hoje: *1*" in message
    assert "AJ-HOJE" in message
    assert "AJ-ONTEM" not in message
    assert "AJ-HOJE\tPonta Grossa\tigorpr050\tPonta Grossa" not in message
