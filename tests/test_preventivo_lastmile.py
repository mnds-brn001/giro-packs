"""Testes do preventivo Last Mile (RESUMO da matriz + copy VENCE HOJE)."""

from __future__ import annotations

import pandas as pd

from src.preventivo_lastmile import (
    RESUMO_FORA,
    RESUMO_VENCE_HOJE,
    apply_hq_resumo,
    build_preventivo_routine,
    preventivo_message_for_base,
)


def test_procv_resumo_from_hq_overrides_local():
    bd = pd.DataFrame(
        {
            "waybill": ["AJ-1", "AJ-2"],
            "status": ["Em rota de entrega", "Pacote armazenado"],
            "last_point": ["RS-W-D076", "RS-W-D076"],
            "courier": ["motoristaA", ""],
            "dest_city": ["Canoas", "Canoas"],
            "resumo": ["VENCE DEPOIS / x", "VENCE DEPOIS / x"],
        }
    )
    hq = pd.DataFrame(
        {
            "waybill": ["AJ-1", "AJ-2"],
            "resumo": [RESUMO_VENCE_HOJE, RESUMO_FORA],
        }
    )
    joined = apply_hq_resumo(bd, hq)
    assert joined.loc[joined["waybill"].eq("AJ-1"), "resumo"].iloc[0] == RESUMO_VENCE_HOJE
    assert joined.loc[joined["waybill"].eq("AJ-2"), "resumo"].iloc[0] == RESUMO_FORA


def test_preventivo_copy_uses_courier_on_route_and_city_on_floor():
    as_of = pd.Timestamp("2026-08-13")
    vence = pd.DataFrame(
        {
            "waybill": ["AJ-ROTA", "AJ-PISO"],
            "status": ["Em rota de entrega", "Recebido no ponto de entrega"],
            "last_track": ["Objeto saiu para entrega ao destinatário", "O pacote já recebido"],
            "courier": ["motoristaSMA", ""],
            "dest_city": ["Canoas", "Guaporé"],
        }
    )
    message = preventivo_message_for_base("RS-W-D076", vence, as_of)

    assert "PRIORIDADE MÁXIMA" in message
    assert "AJ-ROTA" in message
    assert "motoristaSMA" in message
    assert "Objeto saiu para entrega ao destinatário" in message
    assert "AJ-PISO" in message
    assert "Guaporé" in message
    assert "NO PISO" in message


def test_inbound_plus_d0_marks_vence_hoje():
    as_of = pd.Timestamp("2026-08-15")
    bd = pd.DataFrame(
        {
            "waybill": ["AJ-D0-HOJE", "AJ-D1-DEPOIS", "AJ-FORA"],
            "status": ["Em rota de entrega", "Em rota de entrega", "Pacote armazenado"],
            "last_point": ["RS-W-D076", "RS-W-D076", "RS-W-D076"],
            "dest_state": ["RS", "RS", "RS"],
            "dest_city": ["Canoas", "Novo Hamburgo", "Canoas"],
            "courier": ["m1", "m2", ""],
            "base_inbound": [
                "2026-08-15 08:00:00",
                "2026-08-15 08:00:00",
                "2026-08-10 08:00:00",
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
    routine = build_preventivo_routine(bd, as_of=as_of, prazo_lookup=lookup)
    assert set(routine.vence_hoje["waybill"]) == {"AJ-D0-HOJE"}
    assert "AJ-FORA" not in routine.messages["RS-W-D076"]


def test_build_preventivo_keeps_only_vence_hoje_in_messages():
    as_of = pd.Timestamp("2026-08-13")
    bd = pd.DataFrame(
        {
            "waybill": ["AJ-HOJE", "AJ-FORA", "AJ-DEPOIS"],
            "status": ["Em rota de entrega", "Em rota de entrega", "Pacote armazenado"],
            "last_point": ["RS-W-D094", "RS-W-D094", "RS-W-D094"],
            "courier": ["a", "b", ""],
            "dest_city": ["Porto Alegre", "Canoas", "Canoas"],
            "resumo": [RESUMO_VENCE_HOJE, RESUMO_FORA, "VENCE DEPOIS / 稍後獲勝"],
        }
    )
    routine = build_preventivo_routine(bd, as_of=as_of)
    assert set(routine.vence_hoje["waybill"]) == {"AJ-HOJE"}
    assert "AJ-HOJE" in routine.messages["RS-W-D094"]
    assert "AJ-FORA" not in routine.messages["RS-W-D094"]
    assert int(routine.pivot["Total Geral"].sum()) == 3
