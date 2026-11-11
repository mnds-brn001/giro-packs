"""A responsabilidade de 10/11 traz etapa, assistentes e tira UF/SAC."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.preventivo_lastmile import load_prazo_book, load_prazo_tables


def _write_book(path: Path, *, with_legacy_columns: bool) -> None:
    etapa = pd.DataFrame(
        {
            "Último rastreio": ["Objeto saiu para entrega ao destinatário"],
            "Etapa da busca": ["LAST MILE 最后一公里"],
        }
    )
    demandas = pd.DataFrame(
        {
            "责任RESPONSABILIDADE": ["Ana Paula Martins"],
            "Demanda": ["Acompanhamento dos vencimentos de acareações"],
            "Demanda Extra → 额外需求": [None],
        }
    )
    prazo = pd.DataFrame(
        {
            "State": ["RS"],
            "City com acento": ["Canoas"],
            "CONCATENAR ": ["RSCanoas"],
            "New Base Code": ["RS-W-D076"],
            "Time Delivery": ["D+0"],
            "TIME DELIVERY DIAS": [0],
            "Responsible": ["Paulo"],
            "Phone": [42984099958],
        }
    )
    resp_cols = {
        "预派送网点(Ponto de Pré-entrega)": ["RS-W-D076", "RS-W-D099"],
        "RESPONSABILIDADE  责任": ["Bruno Mendes", "Desativado"],
        "操作类TIPO DE OPERAÇÃO": ["DSP", "DSP"],
    }
    if with_legacy_columns:
        resp_cols = {
            "预派送网点(Ponto de Pré-entrega)": ["RS-W-D076"],
            "UF": ["RS"],
            "SAC": ["Ana"],
            "RESPONSABILIDADE  责任": ["Bruno Mendes"],
            "操作类TIPO DE OPERAÇÃO": ["DSP"],
        }
    with pd.ExcelWriter(path) as writer:
        etapa.to_excel(writer, sheet_name="RESP. ETAPA", index=False)
        demandas.to_excel(writer, sheet_name="Demadas", index=False, startrow=2)
        prazo.to_excel(writer, sheet_name="Last Mile 时效", index=False)
        pd.DataFrame(resp_cols).to_excel(writer, sheet_name=" 责任RESPONSABILIDADE", index=False)


def test_new_book_keeps_etapa_off_the_carteira_and_reads_assistentes(tmp_path: Path):
    book_path = tmp_path / "Responsabilidade 10.11.2026.xlsx"
    _write_book(book_path, with_legacy_columns=False)

    book = load_prazo_book(book_path)
    lookup, carteira = load_prazo_tables(book_path)

    assert lookup.loc[0, "base_n"] == "RS-W-D076"
    assert int(lookup.loc[0, "lm_dias"]) == 0
    assert lookup.loc[0, "contato"] == "Paulo"
    assert lookup.loc[0, "telefone"] == "42984099958"
    assert set(carteira["responsavel"]) == {"Bruno Mendes", "Desativado"}
    assert "uf" not in carteira.columns
    assert carteira.loc[carteira["base"].eq("RS-W-D076"), "tipo_operacao"].iloc[0] == "DSP"
    assert book.etapa.loc[0, "etapa"] == "LAST MILE 最后一公里"
    assert book.assistentes.loc[0, "assistente"] == "Ana Paula Martins"
    assert "acareações" in book.assistentes.loc[0, "demanda"]


def test_legacy_book_still_reads_uf_and_sac(tmp_path: Path):
    book_path = tmp_path / "Responsabilidade 30.10.2026.xlsx"
    _write_book(book_path, with_legacy_columns=True)

    _, carteira = load_prazo_tables(book_path)

    assert carteira.loc[0, "responsavel"] == "Bruno Mendes"
    assert carteira.loc[0, "uf"] == "RS"
    assert carteira.loc[0, "sac"] == "Ana"
    assert "RS" not in set(carteira["responsavel"])
