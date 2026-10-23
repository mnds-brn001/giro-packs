r"""HTML do corte D+1 (D0 com inbound depois das 08:00).

Ainda não entra no pack do dia. Ver docs/CORTE_8H.md.

    python scripts\generate_corte_8h.py --as-of 2026-10-22
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
from src.corte_8h import build_corte_report, render_corte_html
from src.ingest import resolve_monitoramento, resolve_prazo, stamp_from_export
from src.preventivo_lastmile import load_prazo_tables


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HTML do corte D+1 pós 08:00.")
    parser.add_argument("--input", default=None, help="XLSX ou parquet. Padrão: data/monitoramento.")
    parser.add_argument("--prazo", default=None, help="Planilha de prazo. Padrão: data/prazo.")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD. Padrão: stamp do arquivo.")
    parser.add_argument("--out", default=None, help="HTML de saída. Padrão: output/lastmile_YYYY-MM-DD/corte_8h.html.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        source = resolve_monitoramento(args.input)
        prazo_path = resolve_prazo(args.prazo)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    as_of = args.as_of or stamp_from_export(source)
    parquet = ROOT / "data" / "parquet" / f"monitoramento_{as_of}.parquet"
    if args.input is None and parquet.is_file():
        source = parquet
    reference = pd.Timestamp(as_of)
    print(f"Corte 8h {as_of}  fonte: {source.name}", flush=True)
    tracking = load_lastmile_source(source, as_of=reference)
    lookup, resp = load_prazo_tables(prazo_path)
    report = build_corte_report(tracking, lookup, resp, reference)
    destination = (
        Path(args.out).expanduser()
        if args.out
        else ROOT / "output" / f"lastmile_{as_of}" / "corte_8h.html"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_corte_html(report), encoding="utf-8")
    print(f"OK: {destination}", flush=True)
    print(
        f"  corte: {report.corte}  saiu de hoje: {report.chegou_hoje}  "
        f"entrou em hoje: {report.chegou_ontem}  saiu do +9: {report.saiu_mais9}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
