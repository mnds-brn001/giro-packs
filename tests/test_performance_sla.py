"""Performance SLA: vencimento do dia, situação e a coluna nova de operação."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from src.ingest import resolve_prazo
from src.performance_sla import (
    SIT_ABERTO,
    SIT_DENTRO,
    SIT_FORA,
    SIT_SEM_PRAZO,
    build_performance_sla,
    classify_sla,
    performance_pivot,
)
from src.preventivo_lastmile import load_prazo_tables


def _lookup() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "dest_state": ["RS", "RS"],
            "dest_city_n": ["canoas", "erechim"],
            "base_n": ["RS-W-D076", "RS-W-D030"],
            "lm_dias": [0, 1],
        }
    )


def _resp() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "base": ["RS-W-D076", "RS-W-D030"],
            "responsavel": ["Bruno Mendes", "Bruno Mendes"],
            "tipo_operacao": ["DSP", "DSP"],
        }
    )


def _row(**overrides) -> dict:
    base = {
        "waybill": "AJ-1",
        "status": "Pedido entregue",
        "region": "Regional Sul",
        "dest_state": "RS",
        "dest_city": "Canoas",
        "pre_point": "RS-W-D076",
        "entry_point": "RS-W-D076",
        "delivery_point": "RS-W-D076",
        "last_point": "RS-W-D076",
        "courier": "motorista",
        "base_inbound": "2026-10-31 07:00:00",
        "signed_at": "2026-10-31 18:00:00",
    }
    base.update(overrides)
    return base


def test_same_day_signature_is_dentro_and_open_package_is_aberto():
    entregues = pd.DataFrame([_row(waybill="AJ-OK")])
    abertos = pd.DataFrame(
        [
            _row(
                waybill="AJ-OPEN",
                status="Em rota de entrega",
                signed_at=None,
                dest_city="Erechim",
                pre_point="RS-W-D030",
                entry_point="RS-W-D030",
                delivery_point="RS-W-D030",
                last_point="RS-W-D030",
                base_inbound="2026-10-30 09:00:00",
            )
        ]
    )
    detail = build_performance_sla(entregues, abertos, _lookup(), _resp(), "2026-10-31")
    by_bill = detail.set_index("waybill")
    assert by_bill.loc["AJ-OK", "situacao"] == SIT_DENTRO
    assert int(by_bill.loc["AJ-OK", "diferenca"]) == 0
    assert by_bill.loc["AJ-OK", "tipo_operacao"] == "DSP"
    assert by_bill.loc["AJ-OPEN", "situacao"] == SIT_ABERTO
    assert pd.isna(by_bill.loc["AJ-OPEN", "diferenca"])


def test_full_extract_keeps_other_days_and_marks_missing_prazo():
    other_day = pd.DataFrame(
        [_row(waybill="AJ-ONTEM", base_inbound="2026-10-30 07:00:00", signed_at="2026-10-30 18:00:00")]
    )
    unknown = pd.DataFrame(
        [_row(waybill="AJ-SEM", dest_city="Cidade Que Nao Existe", dest_state="RS")]
    )
    classified = classify_sla(pd.concat([other_day, unknown], ignore_index=True), None, _lookup(), _resp(), "2026-10-31")
    by_bill = classified.set_index("waybill")
    assert by_bill.loc["AJ-ONTEM", "situacao"] == SIT_DENTRO
    assert bool(by_bill.loc["AJ-ONTEM", "vence_no_dia"]) is False
    assert by_bill.loc["AJ-SEM", "situacao"] == SIT_SEM_PRAZO
    day = build_performance_sla(pd.concat([other_day, unknown], ignore_index=True), None, _lookup(), _resp(), "2026-10-31")
    assert day.empty


def test_d0_after_8h_leaves_the_day_and_next_day_signature_is_fora():
    late_inbound = pd.DataFrame([_row(waybill="AJ-CORTE", base_inbound="2026-10-31 10:00:00")])
    stayed = build_performance_sla(late_inbound, None, _lookup(), _resp(), "2026-10-31")
    assert stayed.empty

    late_sign = pd.DataFrame([_row(waybill="AJ-FORA", signed_at="2026-11-01 09:00:00")])
    detail = build_performance_sla(late_sign, None, _lookup(), _resp(), "2026-10-31")
    assert detail.loc[0, "situacao"] == SIT_FORA
    assert int(detail.loc[0, "diferenca"]) == -1


def test_pivot_matches_the_thursday_layout():
    detail = pd.DataFrame(
        {
            "base": ["RS-W-D076", "RS-W-D076", "RS-W-D076"],
            "tipo_operacao": ["DSP", "DSP", "DSP"],
            "situacao": [SIT_DENTRO, SIT_ABERTO, SIT_FORA],
        }
    )
    pivot = performance_pivot(detail)
    row = pivot.iloc[0]
    assert row["Grupo"] == "RS-W-D076"
    assert int(row["Dentro"]) == 1
    assert int(row["Aberto"]) == 1
    assert int(row["Fora"]) == 1
    assert int(row["Total"]) == 3


def test_prazo_reads_tipo_operacao_and_not_the_uf_column(tmp_path: Path):
    book = tmp_path / "Responsabilidade 30.10.2026.xlsx"
    prazo = pd.DataFrame(
        {
            "State": ["RS"],
            "City com acento": ["Canoas"],
            "CONCATENAR ": ["RSCanoas"],
            "New Base Code": ["RS-W-D076"],
            "Time Delivery": ["D+0"],
            "TIME DELIVERY DIAS": [0],
        }
    )
    resp = pd.DataFrame(
        {
            "预派送网点(Ponto de Pré-entrega)": ["RS-W-D076"],
            "UF": ["RS"],
            "SAC": ["Ana"],
            "RESPONSABILIDADE  责任": ["Bruno Mendes"],
            "操作类TIPO DE OPERAÇÃO": ["DSP"],
        }
    )
    with pd.ExcelWriter(book, engine="xlsxwriter") as writer:
        prazo.to_excel(writer, sheet_name="Last Mile 时效", index=False)
        resp.to_excel(writer, sheet_name=" 责任RESPONSABILIDADE", index=False)

    lookup, carteira = load_prazo_tables(book)
    assert lookup.loc[0, "lm_dias"] == 0
    assert carteira.loc[0, "base"] == "RS-W-D076"
    assert carteira.loc[0, "responsavel"] == "Bruno Mendes"
    assert carteira.loc[0, "tipo_operacao"] == "DSP"
    assert carteira.loc[0, "uf"] == "RS"
    assert "RS" not in set(carteira["responsavel"])


def test_resolve_prazo_prefers_newer_responsabilidade_file(tmp_path: Path):
    prazo_dir = tmp_path / "data" / "prazo"
    prazo_dir.mkdir(parents=True)
    old = prazo_dir / "prazo e responsabilidade last mile 08.09.2026.xlsx"
    new = prazo_dir / "Responsabilidade 30.10.2026.xlsx"
    old.write_bytes(b"PK")
    new.write_bytes(b"PK")
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(new, (1_800_000_000, 1_800_000_000))
    assert resolve_prazo(root=tmp_path) == new.resolve()
