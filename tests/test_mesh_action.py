"""Painel de ação de desvio de malha — tarefa, não métrica."""

from __future__ import annotations

import pandas as pd

from src.mesh_action import (
    ACAO_DEVOLVER_HUB,
    ACAO_ENCAMINHAR,
    ACAO_NAO_ENTREGAR,
    PRIORIDADE_ALTA,
    PRIORIDADE_CRITICA,
    PRIORIDADE_MEDIA,
    build_mesh_action,
    painel_acao,
)


def _row(**overrides) -> dict:
    base = {
        "waybill": "AJ-MESH-1",
        "status": "Recebido no ponto de entrega",
        "is_open": True,
        "pre_point": "RS-W-A002",
        "entry_point": "RS-W-D058",
        "last_point": "RS-W-D058",
        "dest_city": "Passo Fundo",
        "region": "Regional Sul",
        "due_at": "2026-08-29 23:59:59",
        "base_inbound": "2026-08-26 09:01:00",
        "delay_days": 13.0,
    }
    base.update(overrides)
    return base


def test_wrong_base_becomes_devolver_hub():
    df = build_mesh_action(
        pd.DataFrame([_row()]),
        as_of=pd.Timestamp("2026-09-12"),
    )
    assert len(df) == 1
    assert df.iloc[0]["Base Atual"] == "RS-W-D058"
    assert df.iloc[0]["Ponto Certo"] == "RS-W-A002"
    assert df.iloc[0]["Ação Requerida"] == ACAO_DEVOLVER_HUB
    assert df.iloc[0]["Prioridade"] == PRIORIDADE_CRITICA


def test_sitting_at_hub_is_forward_to_pre():
    df = build_mesh_action(
        pd.DataFrame(
            [
                _row(
                    last_point="RS-W-H001",
                    entry_point="RS-W-H001",
                    pre_point="RS-W-D044",
                    delay_days=2,
                    base_inbound="2026-09-10 08:00:00",
                )
            ]
        ),
        as_of=pd.Timestamp("2026-09-12"),
    )
    assert df.iloc[0]["Ação Requerida"] == ACAO_ENCAMINHAR


def test_in_route_at_wrong_base_do_not_deliver():
    df = build_mesh_action(
        pd.DataFrame([_row(status="Em rota de entrega", delay_days=2.0, base_inbound="2026-09-10 08:00:00")]),
        as_of=pd.Timestamp("2026-09-12"),
    )
    assert df.iloc[0]["Ação Requerida"] == ACAO_NAO_ENTREGAR


def test_correct_mesh_is_omitted():
    df = build_mesh_action(
        pd.DataFrame(
            [_row(pre_point="RS-W-D058", entry_point="RS-W-D058", last_point="RS-W-D058")]
        ),
        as_of=pd.Timestamp("2026-09-12"),
    )
    assert df.empty


def test_delivered_is_omitted():
    df = build_mesh_action(
        pd.DataFrame([_row(status="Pedido entregue", is_open=False)]),
        as_of=pd.Timestamp("2026-09-12"),
    )
    assert df.empty


def test_priority_media_when_still_on_time():
    df = build_mesh_action(
        pd.DataFrame(
            [
                _row(
                    due_at="2026-09-20 23:59:59",
                    delay_days=0,
                    base_inbound="2026-09-11 10:00:00",
                )
            ]
        ),
        as_of=pd.Timestamp("2026-09-12"),
    )
    assert df.iloc[0]["Prioridade"] == PRIORIDADE_MEDIA


def test_priority_alta_when_due_passed_but_not_parked():
    df = build_mesh_action(
        pd.DataFrame(
            [
                _row(
                    due_at="2026-09-11 23:59:59",
                    delay_days=0.4,
                    base_inbound="2026-09-11 08:00:00",
                )
            ]
        ),
        as_of=pd.Timestamp("2026-09-12"),
    )
    assert df.iloc[0]["Prioridade"] == PRIORIDADE_ALTA


def test_action_kpis_and_excel_bytes():
    from src.mesh_action import action_kpis, painel_excel_bytes

    df = build_mesh_action(pd.DataFrame([_row()]), as_of=pd.Timestamp("2026-09-12"))
    kpis = action_kpis(df)
    assert kpis["piso"] == 1
    assert kpis["critica"] == 1
    raw = painel_excel_bytes(df)
    assert raw[:2] == b"PK"


def test_painel_has_no_sla_metrics():
    detail = build_mesh_action(pd.DataFrame([_row()]), as_of=pd.Timestamp("2026-09-12"))
    painel = painel_acao(detail, com_filtro=False)
    assert list(painel.columns) == [
        "Waybill",
        "Base Atual",
        "Ponto Certo",
        "Ação Requerida",
        "Prioridade",
    ]
    assert "delay_days" not in painel.columns


def test_owner_of_current_base_executes():
    resp = pd.DataFrame(
        [
            {"base": "RS-W-D058", "responsavel": "Bruno Mendes"},
            {"base": "RS-W-A002", "responsavel": "Matheus Raymundo"},
        ]
    )
    df = build_mesh_action(
        pd.DataFrame([_row()]),
        as_of=pd.Timestamp("2026-09-12"),
        responsabilidade=resp,
    )
    assert df.iloc[0]["Responsável"] == "Bruno Mendes"
    assert df.iloc[0]["Dono do ponto certo"] == "Matheus Raymundo"
