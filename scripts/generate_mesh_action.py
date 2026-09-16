r"""Painel de ação diária — desvios de malha (pré × ponto físico).

Exemplo:
    python scripts/generate_mesh_action.py --as-of 2026-09-12 --out output\lastmile_2026-09-12
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backlog_lastmile import load_lastmile_source
from src.ingest import ingest_monitoramento, resolve_prazo
from src.mesh_action import PRIORIDADE_CRITICA, build_mesh_action, write_mesh_excel
from src.preventivo_lastmile import load_prazo_tables


def _slug(name: str) -> str:
    import re

    cleaned = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE)
    return cleaned.strip("_")[:80] or "SEM_RESPONSAVEL"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Painel de ação de desvios de malha.")
    parser.add_argument("--input", default=None)
    parser.add_argument("--prazo", default=None)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--out", default="output/lastmile")
    parser.add_argument("--region", default="Regional Sul")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--force-ingest", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.skip_ingest:
            from src.ingest import resolve_monitoramento

            source = resolve_monitoramento(args.input)
        else:
            ingest = ingest_monitoramento(args.input, force=args.force_ingest)
            source = ingest.parquet
            print(f"Parquet: {ingest.parquet.name}", flush=True)
        prazo_path = resolve_prazo(args.prazo)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    reference = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp.now()
    tracking = load_lastmile_source(source, as_of=reference)
    _, resp = load_prazo_tables(prazo_path)
    detail = build_mesh_action(
        tracking,
        as_of=reference,
        responsabilidade=resp,
        region=args.region or None,
    )
    out = Path(args.out).expanduser()
    stamp = pd.Timestamp(reference).strftime("%Y-%m-%d")
    dest = out / "malha_acao" / f"painel_malha_{stamp}.xlsx"
    write_mesh_excel(detail, dest)
    n_crit = int(detail["Prioridade"].eq(PRIORIDADE_CRITICA).sum()) if len(detail) else 0
    if "Responsável" in detail.columns:
        for owner in sorted({str(x).strip() for x in detail["Responsável"].dropna() if str(x).strip()}):
            part = detail.loc[detail["Responsável"].astype("string").str.contains(owner, case=False, na=False)]
            write_mesh_excel(part, out / _slug(owner) / f"malha_acao_{stamp}.xlsx")
    print(f"OK: {dest}  ({len(detail)} desvios, {n_crit} critica)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
