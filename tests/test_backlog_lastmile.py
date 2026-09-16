"""Testes da rotina diária de exceções Last Mile."""

from __future__ import annotations

import pandas as pd

from src.backlog_lastmile import LAST_MILE_KEYS, build_lastmile_routine, load_lastmile_source
from src.columns import COL
from src.loader import enrich
from tests.helpers import make_raw_row


def test_routine_separates_route_and_floor_due_today(as_of):
    rows = [
        make_raw_row(
            waybill="AJ-ROTA-HOJE",
            status="Em rota de entrega",
            entry_point="RS-W-D027",
            courier="motoristaA",
            due_at="2026-08-01 20:00:00",
            delay_days=12,
        ),
        make_raw_row(
            waybill="AJ-PISO-HOJE",
            status="Pacote armazenado",
            entry_point="RS-W-D027",
            due_at="2026-08-01 22:00:00",
            delay_days=3,
        ),
        make_raw_row(
            waybill="AJ-ROTA-AMANHA",
            status="Em rota de entrega",
            entry_point="RS-W-D027",
            due_at="2026-08-02 10:00:00",
            delay_days=1,
        ),
        make_raw_row(
            waybill="AJ-ENTREGUE",
            status="Pedido entregue",
            entry_point="RS-W-D027",
            due_at="2026-08-01 10:00:00",
        ),
    ]
    routine = build_lastmile_routine(enrich(pd.DataFrame(rows), as_of=as_of), as_of=as_of)

    assert routine.due_today_route["waybill"].tolist() == ["AJ-ROTA-HOJE"]
    assert routine.due_today_floor["waybill"].tolist() == ["AJ-PISO-HOJE"]
    assert set(routine.regional_backlog["waybill"]) == {
        "AJ-ROTA-HOJE",
        "AJ-PISO-HOJE",
        "AJ-ROTA-AMANHA",
    }


def test_routine_groups_backlog_into_disjoint_age_buckets(as_of):
    rows = [
        make_raw_row(waybill="AJ-16", delay_days=16, entry_point="RS-W-D027"),
        make_raw_row(waybill="AJ-11", delay_days=11, entry_point="RS-W-D027"),
        make_raw_row(waybill="AJ-06", delay_days=6, entry_point="RS-W-D027"),
        make_raw_row(waybill="AJ-02", delay_days=2, entry_point="RS-W-D027"),
    ]
    routine = build_lastmile_routine(enrich(pd.DataFrame(rows), as_of=as_of), as_of=as_of)
    summary = routine.base_summary.iloc[0]

    assert summary["Mais de 15 dias de atraso"] == 1
    assert summary["Mais de 10 dias de atraso"] == 1
    assert summary["Mais de 5 dias de atraso"] == 1
    assert summary["Menos de 4 dias"] == 1
    assert summary["Total Geral"] == 4


def test_backlog_message_lists_floor_city_and_route_courier(as_of):
    rows = [
        make_raw_row(
            waybill="AJ-PISO-CRITICO",
            status="Pacote armazenado",
            entry_point="RS-W-D011",
            dest_city="Canoas",
            delay_days=16,
        ),
        make_raw_row(
            waybill="AJ-ROTA-CRITICO",
            status="Em rota de entrega",
            entry_point="RS-W-D011",
            courier="motoristaSMA",
            dest_city="Gravataí",
            delay_days=12,
        ),
        make_raw_row(
            waybill="AJ-RECENTE",
            status="Pacote armazenado",
            entry_point="RS-W-D011",
            dest_city="Porto Alegre",
            delay_days=2,
        ),
    ]
    routine = build_lastmile_routine(enrich(pd.DataFrame(rows), as_of=as_of), as_of=as_of)
    message = routine.messages["RS-W-D011"]

    assert "BACKLOG – TRATATIVA" in message
    assert "AJ-PISO-CRITICO" in message
    assert "Canoas" in message
    assert "AJ-ROTA-CRITICO" in message
    assert "motoristaSMA" in message
    assert "Gravataí" in message
    assert "AJ-RECENTE" not in message
    assert "AJs +10d para copiar" in message
    copy_block = message.split("AJs +10d para copiar")[1]
    assert "AJ-PISO-CRITICO" in copy_block
    assert "AJ-ROTA-CRITICO" in copy_block
    assert "\nAJ-PISO-CRITICO\n" in copy_block or copy_block.strip().startswith("AJ-PISO") or "AJ-PISO-CRITICO\n" in copy_block
    assert "PRIORIDADE MÁXIMA" not in message
    assert "PRIORIDADE MÁXIMA" in routine.preventivo_messages["RS-W-D011"]


def test_routine_can_filter_by_base_inbound_window(as_of):
    rows = [
        make_raw_row(
            waybill="AJ-INBOUND-HOJE",
            entry_point="RS-W-D027",
            base_inbound="2026-08-01 08:00:00",
        ),
        make_raw_row(
            waybill="AJ-INBOUND-ANTIGO",
            entry_point="RS-W-D027",
            base_inbound="2026-07-01 08:00:00",
        ),
    ]
    routine = build_lastmile_routine(
        enrich(pd.DataFrame(rows), as_of=as_of),
        as_of=as_of,
        inbound_from="2026-08-01",
        inbound_to="2026-08-01",
    )

    assert routine.regional_backlog["waybill"].tolist() == ["AJ-INBOUND-HOJE"]


def test_load_lastmile_source_reads_only_needed_columns(tmp_path, as_of):
    rows = [
        make_raw_row(
            waybill="AJ-FAST",
            status="Em rota de entrega",
            due_at="2026-08-01 18:00:00",
            base_inbound="2026-08-01 08:00:00",
            entry_point="RS-W-D027",
            courier="motoristaA",
        )
    ]
    frame = pd.DataFrame(rows).rename(columns={k: COL[k] for k in rows[0] if k in COL})
    path = tmp_path / "fast.xlsx"
    frame.to_excel(path, index=False)

    loaded = load_lastmile_source(path, as_of=as_of)
    assert "AJ-FAST" in set(loaded["waybill"].astype(str))
    assert set(loaded.columns) <= set(LAST_MILE_KEYS) | {"is_open"}
    assert bool(loaded.loc[0, "is_open"]) is True
