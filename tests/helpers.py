"""Builders de dados para testes."""

from __future__ import annotations


def make_raw_row(**overrides) -> dict:
    """Linha canônica mínima válida para validação + enrich."""
    base = {
        "waybill": "AJ26000001",
        "status": "Em rota de entrega",
        "pre_point": "RS-W-D033",
        "entry_point": "RS-W-D033",
        "delivery_point": "RS-W-D033",
        "delay_days": 0.0,
        "closed_loop_days": 45.0,
        "sla_days": 7.0,
        "due_at": "2026-07-10 12:00:00",
        "closed_loop_at": "2026-08-20 12:00:00",
        "dest_city": "Porto Alegre",
        "city_type": "METROPOLITAN",
        "client": "demo_client",
        "merchant": "DEMO MERCHANT",
        "attempt_1": "--",
        "attempt_2": "--",
        "attempt_3": "--",
        "hub1_op_h": 2.0,
        "hub2_op_h": 0.0,
        "hub3_op_h": 0.0,
        "hub1_transit_h": 1.0,
        "hub2_transit_h": 0.0,
        "hub3_transit_h": 0.0,
        "base_dispatch_h": 1.0,
        "dispatch_transit_h": 0.5,
        "base_sign_h": 0.5,
    }
    base.update(overrides)
    return base
