r"""Converte a Tabela Mestra (Excel/CSV) para Parquet.

Sem ``--out``, grava em data/parquet/monitoramento_YYYY-MM-DD.parquet e
copia para monitoramento_latest.parquet.

Exemplo:
    python scripts/xlsx_to_parquet.py
    python scripts/xlsx_to_parquet.py --input "data\monitoramento\monitoramento.xlsx"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ingest import ingest_monitoramento
from src.loader import convert_tracking_to_parquet


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Converte a Tabela Mestra Excel/CSV para Parquet (mesmo schema de colunas)."
    )
    parser.add_argument(
        "--input",
        default=None,
        help="XLSX/CSV de origem (padrão: data/monitoramento ou raiz).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Destino explícito. Sem este flag, usa data/parquet/.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reconverte mesmo se o Parquet datado já estiver atualizado.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.out:
        if not args.input:
            raise SystemExit("Com --out, informe --input.")
        source = Path(args.input)
        if not source.exists():
            raise SystemExit(f"Arquivo não encontrado: {source}")
        dest = convert_tracking_to_parquet(source, args.out)
    else:
        try:
            result = ingest_monitoramento(args.input, force=args.force)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
        dest = result.parquet
        print(f"Fonte: {result.source}")
        print(f"Torre: {result.latest}")
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"Parquet gravado: {dest} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
