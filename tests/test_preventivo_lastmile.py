"""Testes do preventivo Last Mile (RESUMO da matriz + copy Fora do Prazo LM +9d)."""

from __future__ import annotations

import pandas as pd

from src.preventivo_lastmile import (
    RESUMO_DEPOIS,
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

    assert "Fora do Prazo LM" in message
    assert "VENCE HOJE" not in message
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
            "waybill": ["AJ-D0-HOJE", "AJ-D1-DEPOIS", "AJ-FORA", "AJ-FORA-9", "AJ-FORA-8"],
            "status": [
                "Em rota de entrega",
                "Em rota de entrega",
                "Pacote armazenado",
                "Em rota de entrega",
                "Pacote armazenado",
            ],
            "last_point": ["RS-W-D076"] * 5,
            "dest_state": ["RS"] * 5,
            "dest_city": ["Canoas", "Novo Hamburgo", "Canoas", "Canoas", "Canoas"],
            "courier": ["m1", "m2", "", "m9", ""],
            "base_inbound": [
                "2026-08-15 08:00:00",
                "2026-08-15 08:00:00",
                "2026-08-10 08:00:00",
                "2026-08-06 08:00:00",
                "2026-08-07 08:00:00",
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
    assert set(routine.fora_lm["waybill"]) == {"AJ-FORA-9"}
    message = routine.messages["RS-W-D076"]
    assert "AJ-FORA-9" in message
    rest = message.replace("AJ-FORA-9", "")
    assert "AJ-D0-HOJE" not in rest
    assert "AJ-FORA-8" not in rest
    assert "AJ-FORA" not in rest


def test_d0_inbound_after_8h_moves_to_next_day():
    as_of = pd.Timestamp("2026-08-15")
    bd = pd.DataFrame(
        {
            "waybill": ["AJ-CEDO", "AJ-TARDE", "AJ-ONTEM-TARDE", "AJ-D1-TARDE"],
            "status": ["Em rota de entrega"] * 4,
            "last_point": ["RS-W-D076"] * 4,
            "dest_state": ["RS"] * 4,
            "dest_city": ["Canoas", "Canoas", "Canoas", "Novo Hamburgo"],
            "courier": ["m"] * 4,
            "issue_reason": ["Cliente ausente", "", "", ""],
            "base_inbound": [
                "2026-08-15 08:00:00",
                "2026-08-15 08:01:00",
                "2026-08-14 10:00:00",
                "2026-08-15 10:00:00",
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
    detail = routine.detail.set_index("waybill")
    assert detail.loc["AJ-CEDO", "resumo"] == RESUMO_VENCE_HOJE
    assert pd.isna(detail.loc["AJ-CEDO", "corte_8h"])
    assert detail.loc["AJ-TARDE", "resumo"] == RESUMO_DEPOIS
    assert detail.loc["AJ-TARDE", "corte_8h"] == "D+1"
    assert detail.loc["AJ-ONTEM-TARDE", "resumo"] == RESUMO_VENCE_HOJE
    assert detail.loc["AJ-ONTEM-TARDE", "corte_8h"] == "D+1"
    assert detail.loc["AJ-D1-TARDE", "resumo"] == RESUMO_DEPOIS
    assert pd.isna(detail.loc["AJ-D1-TARDE", "corte_8h"])
    assert "base_inbound" in routine.detail.columns
    assert "issue_reason" in routine.detail.columns


def test_build_preventivo_messages_keep_only_fora_lm_plus_9():
    as_of = pd.Timestamp("2026-08-13")
    bd = pd.DataFrame(
        {
            "waybill": ["AJ-HOJE", "AJ-FORA-9", "AJ-FORA-8", "AJ-DEPOIS"],
            "status": ["Em rota de entrega", "Em rota de entrega", "Pacote armazenado", "Pacote armazenado"],
            "last_point": ["RS-W-D094"] * 4,
            "courier": ["a", "b", "", ""],
            "dest_city": ["Porto Alegre", "Canoas", "Canoas", "Canoas"],
            "resumo": [RESUMO_VENCE_HOJE, RESUMO_FORA, RESUMO_FORA, "VENCE DEPOIS / 稍後獲勝"],
            "dias_fora": [0, 9, 8, -2],
        }
    )
    routine = build_preventivo_routine(bd, as_of=as_of)
    assert set(routine.vence_hoje["waybill"]) == {"AJ-HOJE"}
    assert set(routine.fora_lm["waybill"]) == {"AJ-FORA-9"}
    assert "AJ-FORA-9" in routine.messages["RS-W-D094"]
    assert "AJ-HOJE" not in routine.messages["RS-W-D094"]
    assert "AJ-FORA-8" not in routine.messages["RS-W-D094"]
    assert "Fora do Prazo LM" in routine.messages["RS-W-D094"]
    assert int(routine.pivot["Total Geral"].sum()) == 4
