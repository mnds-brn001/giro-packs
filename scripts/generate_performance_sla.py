"""Reparte a extração de entregues por responsável, no mesmo ritual dos outros packs.

Larga o xlsx em data\\entregues (e a operacional já fica em data\\monitoramento).
Um comando devolve a DIN de quem vence no dia e, em cada carteira, a auditoria
da extração inteira com a situação: dentro, fora ou em aberto.

    python scripts\\generate_performance_sla.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ingest import ingest_entregues, resolve_monitoramento, resolve_prazo, stamp_from_export
from src.paths import PARQUET_DIR
from src.performance_sla import (
    SIT_ABERTO,
    SIT_DENTRO,
    SIT_FORA,
    SIT_SEM_PRAZO,
    classify_sla,
    load_tracking_slice,
    performance_pivot,
    write_owner_books,
    write_performance_book,
)
from src.preventivo_lastmile import load_prazo_tables


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pack de performance SLA por responsável.")
    parser.add_argument("--entregues", default=None, help="XLSX/Parquet de Pedido entregue (padrão: data/entregues).")
    parser.add_argument("--abertos", default=None, help="Operacional ainda sem assinatura (padrão: monitoramento_latest).")
    parser.add_argument("--prazo", default=None, help="Planilha de responsabilidade. Padrão: data/prazo.")
    parser.add_argument("--dia", default=None, help="YYYY-MM-DD da DIN. Padrão: stamp da extração de entregues.")
    parser.add_argument("--region", default="Regional Sul")
    parser.add_argument("--out", default=None, help="Pasta de saída.")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--force-ingest", action="store_true")
    return parser.parse_args()


def _resolve_abertos(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Abertos não encontrado: {path}")
        return path.resolve()
    latest = PARQUET_DIR / "monitoramento_latest.parquet"
    if latest.is_file():
        return latest.resolve()
    try:
        return resolve_monitoramento(None)
    except FileNotFoundError:
        return None


def main() -> int:
    args = parse_args()
    try:
        if args.skip_ingest:
            entregues_path = Path(args.entregues).expanduser().resolve() if args.entregues else None
            if entregues_path is None or not entregues_path.is_file():
                raise FileNotFoundError("Com --skip-ingest, informe --entregues apontando para o parquet.")
        else:
            print("Ingerindo entregues...", flush=True)
            ingested = ingest_entregues(args.entregues, force=args.force_ingest)
            entregues_path = ingested.parquet
            verb = "já atualizado" if ingested.skipped else "convertido"
            print(f"  {verb}: {ingested.parquet.name}", flush=True)
        abertos_path = _resolve_abertos(args.abertos)
        prazo_path = resolve_prazo(args.prazo)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    dia = pd.Timestamp(args.dia).normalize() if args.dia else pd.Timestamp(stamp_from_export(entregues_path))
    out = Path(args.out).expanduser() if args.out else ROOT / "output" / f"performance_sla_{dia.date().isoformat()}"

    print(f"Prazo: {prazo_path.name}", flush=True)
    prazo_lookup, responsabilidade = load_prazo_tables(prazo_path)
    tipo = "tipo_operacao" in responsabilidade.columns
    print(
        f"  Carteira: {responsabilidade['base'].nunique()} bases"
        f" · operação {'sim' if tipo else 'não'}",
        flush=True,
    )
    print(f"Entregues: {entregues_path.name}", flush=True)
    entregues = load_tracking_slice(entregues_path)
    abertos = None
    if abertos_path is not None:
        print(f"Abertos: {abertos_path.name}", flush=True)
        abertos = load_tracking_slice(abertos_path)

    print("Classificando a extração inteira...", flush=True)
    classified = classify_sla(
        entregues,
        abertos,
        prazo_lookup,
        responsabilidade,
        dia,
        region=args.region or None,
    )
    if classified.empty:
        print("Nenhum pacote na extração.", flush=True)
        return 0
    day = classified.loc[classified["vence_no_dia"]].copy()
    title = f"PERFORMANCE SLA LM {dia.strftime('%d.%m')}"
    regional = out / f"PERFORMANCE_SLA_LM_{dia.date().isoformat()}.xlsx"
    carteira = performance_pivot(classified, group="responsavel")
    write_performance_book(regional, day, title=title, include_detail=False, carteira=carteira)
    books = write_owner_books(day, out, dia=dia, audit=classified)
    dentro = int(classified["situacao"].eq(SIT_DENTRO).sum())
    aberto = int(classified["situacao"].eq(SIT_ABERTO).sum())
    fora = int(classified["situacao"].eq(SIT_FORA).sum())
    sem = int(classified["situacao"].eq(SIT_SEM_PRAZO).sum())
    print(
        f"  Extração {len(classified)} · dentro {dentro} · aberto {aberto} · fora {fora} · sem prazo {sem}",
        flush=True,
    )
    print(
        f"  DIN {dia.date().isoformat()}: {len(day)} pacotes vencem no dia",
        flush=True,
    )
    print(f"Regional: {regional}", flush=True)
    print(f"Carteiras: {len(books)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
