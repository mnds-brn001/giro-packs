"""Cobrança de perda de rastreio a partir do controle de backlog."""

from __future__ import annotations

import pandas as pd

from src.perda_rastreio import (
    attach_carteira,
    build_perda_rastreio,
    classificar_evento,
    map_perda_columns,
    normalize_perda,
    resolve_perda_rastreio,
    stamp_from_controle,
)


def test_classificar_evento_separa_os_tres_mecanismos():
    assert classificar_evento("O veículo foi desbloqueado em [SC-W-D077]") == "veiculo"
    assert classificar_evento("O pacote saiu de [RS-W-D008] e foi devolvido ao centro de trânsito de [RS-W-H001]") == "voltou hub"
    assert classificar_evento("O pacote saiu do centro de transbordo [PR-W-H001] e foi enviado para [SP-RR-002]") == "expedido hub"
    assert classificar_evento("O pacote já recebido pelo ponto de entrega[RS-W-D070]") == "no ponto"
    assert classificar_evento("Objeto saiu para entrega ao destinatário") == "em rota"


def test_map_columns_le_rotulo_portugues_do_controle():
    columns = [
        "运单号(Número do Waybill)",
        "滞留天数(Dias de Retenção)",
        "最新轨迹内容(Último rastreio)",
        "轨迹停更网点(Ponto de parada)",
        "责任RESPONSABILIDADE",
    ]
    found = map_perda_columns(columns)
    assert found["waybill"] == columns[0]
    assert found["dias"] == columns[1]
    assert found["ponto"] == columns[3]
    assert found["resp"] == columns[4]


def test_resolve_perda_rastreio_pega_o_xlsx_da_pasta(tmp_path):
    folder = tmp_path / "data" / "monitoramento da perda de rastreio"
    folder.mkdir(parents=True)
    older = folder / "pacotes parados-20261007000000000-a.xlsx"
    newer = folder / "pacotes parados-20261008000000000-b.xlsx"
    older.write_bytes(b"PK")
    newer.write_bytes(b"PK")
    import os
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))
    assert resolve_perda_rastreio(root=tmp_path) == newer.resolve()


def test_stamp_from_controle_le_data_do_nome():
    assert stamp_from_controle("控制积压CONTROLE BACKLOG SUL 06.10.2026_18H00.xlsx") == "2026-10-06"


def _frame() -> pd.DataFrame:
    rows = [
        ("AJ-D077-1", 30, "O veículo foi desbloqueado em [SC-W-D077]", "SC-W-D077", "Andrielli Folster", "2026-09-02", "Balneário Camboriú"),
        ("AJ-D077-2", 30, "O veículo foi desbloqueado em [SC-W-D077]", "SC-W-D077", "Andrielli Folster", "2026-09-02", "Camboriú"),
        ("AJ-D077-3", 30, "O veículo foi desbloqueado em [SC-W-D077]", "SC-W-D077", "Andrielli Folster", "2026-09-02", "Camboriú"),
        ("AJ-D077-4", 30, "O veículo foi desbloqueado em [SC-W-D077]", "SC-W-D077", "Andrielli Folster", "2026-09-02", "Camboriú"),
        ("AJ-D077-5", 30, "O veículo foi desbloqueado em [SC-W-D077]", "SC-W-D077", "Andrielli Folster", "2026-09-02", "Camboriú"),
        ("AJ-D077-NOVO", 7, "Objeto saiu para entrega ao destinatário", "SC-W-D077", "Andrielli Folster", "2026-09-29", "Camboriú"),
        ("AJ-D008-1", 50, "O pacote saiu de [RS-W-D008] e foi devolvido ao centro de trânsito de [RS-W-H001]", "RS-W-D008", "Andrielli Folster", "2026-07-10", "Caxias do Sul"),
        ("AJ-D008-2", 50, "O pacote saiu de [RS-W-D008] e foi devolvido ao centro de trânsito de [RS-W-H001]", "RS-W-D008", "Andrielli Folster", "2026-07-20", "Caxias do Sul"),
        ("AJ-D008-3", 50, "O pacote saiu de [RS-W-D008] e foi devolvido ao centro de trânsito de [RS-W-H001]", "RS-W-D008", "Andrielli Folster", "2026-08-01", "Farroupilha"),
        ("AJ-D008-4", 50, "O pacote saiu de [RS-W-D008] e foi devolvido ao centro de trânsito de [RS-W-H001]", "RS-W-D008", "Andrielli Folster", "2026-08-10", "Vacaria"),
        ("AJ-D008-5", 50, "O pacote saiu de [RS-W-D008] e foi devolvido ao centro de trânsito de [RS-W-H001]", "RS-W-D008", "Andrielli Folster", "2026-08-15", "Bento Gonçalves"),
        ("AJ-CAUDA", 50, "O veículo foi desbloqueado em [RS-W-D043]", "RS-W-D043", "Bruno Mendes", "2026-07-01", "Santa Maria"),
    ]
    raw = pd.DataFrame(
        rows,
        columns=["waybill", "dias", "ultimo", "ponto", "resp", "track_at", "cidade"],
    )
    raw["pre"] = raw["ponto"]
    raw["status"] = ""
    raw["cliente"] = ""
    raw["track_at"] = pd.to_datetime(raw["track_at"])
    raw["evento"] = raw["ultimo"].map(classificar_evento)
    return raw


def test_mensagem_de_desbloqueio_cita_o_dia_e_deixa_o_giro_recente_de_fora():
    routine = build_perda_rastreio(_frame(), as_of="2026-10-06")
    texto = routine.messages["SC-W-D077"]
    assert "desbloqueio do veículo" in texto
    assert "em 02/09/2026" in texto
    assert "AJ-D077-1" in texto
    assert "AJ-D077-NOVO" not in texto
    assert "7–15 dias" in texto
    assert "RS-W-D043" not in routine.messages


def test_mensagem_de_hub_usa_intervalo_quando_o_bip_nao_e_um_dia_so():
    routine = build_perda_rastreio(_frame(), as_of="2026-10-06")
    texto = routine.messages["RS-W-D008"]
    assert "RS-W-H001" in texto
    assert "entre 10/07/2026 e 15/08/2026" in texto
    assert "AJ-D008-1" in texto


def test_filtro_de_carteira_nao_cobra_a_outra():
    routine = build_perda_rastreio(_frame(), as_of="2026-10-06", responsavel="Bruno Mendes")
    assert routine.messages == {}
    assert set(routine.grave["waybill"]) == {"AJ-CAUDA"}


def test_hub_nao_entra_na_cobranca_da_base():
    frame = _frame()
    extra = pd.DataFrame(
        {
            "waybill": [f"AJ-HUB-{i}" for i in range(5)],
            "dias": [50] * 5,
            "ultimo": ["O pacote saiu do centro de transbordo [PR-W-H001] e foi enviado para [SP-RR-002]"] * 5,
            "ponto": ["PR-W-H001"] * 5,
            "resp": ["Andre Alax"] * 5,
            "track_at": pd.to_datetime(["2026-08-01"] * 5),
            "cidade": ["Curitiba"] * 5,
            "pre": ["PR-W-D050"] * 5,
            "status": [""] * 5,
            "cliente": [""] * 5,
            "evento": ["expedido hub"] * 5,
        }
    )
    routine = build_perda_rastreio(pd.concat([frame, extra], ignore_index=True), as_of="2026-10-06")
    assert "PR-W-H001" not in routine.messages
    assert "SC-W-D077" in routine.messages
    assert routine.hubs.loc[routine.hubs["ponto"].eq("PR-W-H001"), "destino"].iloc[0] == "SP-RR-002"
    assert "Não entra na cobrança da base" in routine.hub_messages["PR-W-H001"]


def test_carteira_vem_do_prazo_quando_o_extract_nao_traz_dono():
    frame = _frame()
    frame["resp"] = "SEM CARTEIRA"
    prazo = pd.DataFrame({"base": ["SC-W-D077"], "responsavel": ["Andrielli Folster"]})
    ligado = attach_carteira(frame, prazo)
    assert ligado.loc[ligado["ponto"].eq("SC-W-D077"), "resp"].iloc[0] == "Andrielli Folster"
    routine = build_perda_rastreio(frame, as_of="2026-10-06", responsabilidade=prazo)
    assert "Andrielli Folster" in routine.messages["SC-W-D077"]


def test_normalize_aceita_cabecalho_bilingue():
    raw = pd.DataFrame(
        {
            "运单号(Número do Waybill)": ["AJ1"],
            "滞留天数(Dias de Retenção)": [30],
            "最新轨迹内容(Último rastreio)": ["O veículo foi desbloqueado em [SC-W-D077]"],
            "轨迹停更网点(Ponto de parada)": ["SC-W-D077"],
            "责任RESPONSABILIDADE": ["Andrielli Folster"],
        }
    )
    work = normalize_perda(raw)
    assert work.loc[0, "waybill"] == "AJ1"
    assert work.loc[0, "evento"] == "veiculo"
    assert work.loc[0, "ponto"] == "SC-W-D077"
