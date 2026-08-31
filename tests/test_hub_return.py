"""Testes da rotina de devolução ao hub superior."""

from __future__ import annotations

import pandas as pd

from src.hub_return import (
    BUCKET_GREEN,
    BUCKET_ORANGE,
    BUCKET_RED,
    BUCKET_YELLOW,
    age_bucket,
    build_hub_return_routine,
    parse_hub_transfer,
)


def test_age_bucket_thresholds():
    assert age_bucket(0) == BUCKET_GREEN
    assert age_bucket(3) == BUCKET_GREEN
    assert age_bucket(4) == BUCKET_YELLOW
    assert age_bucket(6) == BUCKET_YELLOW
    assert age_bucket(7) == BUCKET_ORANGE
    assert age_bucket(9) == BUCKET_ORANGE
    assert age_bucket(10) == BUCKET_RED
    assert age_bucket(26) == BUCKET_RED


def test_parse_hub_transfer_extracts_origin_and_hub():
    origin, hub = parse_hub_transfer(
        "O pacote saiu de [PR-W-D015] e foi devolvido ao centro de trânsito de [PR-W-H001]"
    )
    assert origin == "PR-W-D015"
    assert hub == "PR-W-H001"


def test_message_lists_critical_waybills_and_asks_physical_dispatch():
    as_of = pd.Timestamp("2026-08-26")
    df = pd.DataFrame(
        {
            "waybill": ["AJ-CRITICO", "AJ-RECENTE"],
            "status": ["Devolvido pelo ponto", "Devolvido pelo ponto"],
            "last_point": ["PR-W-D015", "PR-W-D015"],
            "last_track": [
                "O pacote saiu de [PR-W-D015] e foi devolvido ao centro de trânsito de [PR-W-H001]",
                "O pacote saiu de [PR-W-D015] e foi devolvido ao centro de trânsito de [PR-W-H001]",
            ],
            "last_track_time": ["2026-08-11", "2026-08-25"],
            "issue_reason": ["Endereço de destinário errado", "Pacote não pertence à Base"],
            "dest_city": ["Paranaguá", "Curitiba"],
            "dias_status": [15, 1],
        }
    )
    routine = build_hub_return_routine(df, as_of=as_of)
    message = routine.messages["PR-W-D015"]
    assert "DEVOLUÇÃO AO HUB SUPERIOR" in message
    assert "PR-W-H001" in message
    assert "AJ-CRITICO" in message
    assert "Paranaguá" in message
    assert "AJ-RECENTE" not in message
    assert "despachado fisicamente" in message.casefold() or "despacho" in message.casefold()
    assert "hub_return" in routine.html or "Devolução ao Hub" in routine.html
