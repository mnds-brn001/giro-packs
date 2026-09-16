r"""Pack do dia: backlog + preventivo + malha por responsável + TikTok.

No PC de viagem:

    python scripts\generate_daily_pack.py --as-of 2026-09-16

Drop zones:
    data/monitoramento/   export Express/SRM (monitoramento da pontualidade…)
    data/prazo/           prazo e responsabilidade last mile.xlsx
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ingest import resolve_monitoramento, stamp_from_export


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera o pack diário last mile + TikTok.")
    parser.add_argument("--input", default=None, help="XLSX/Parquet (padrão: data/monitoramento).")
    parser.add_argument("--prazo", default=None, help="Planilha de prazo (padrão: data/prazo).")
    parser.add_argument(
        "--as-of",
        default=None,
        help="YYYY-MM-DD. Se omitido, usa o stamp 20YYMMDD do filename da extração.",
    )
    parser.add_argument("--out", default=None, help="Pasta de saída (padrão: output/lastmile_YYYY-MM-DD).")
    parser.add_argument("--region", default="Regional Sul")
    parser.add_argument("--force-ingest", action="store_true")
    parser.add_argument("--skip-tiktok", action="store_true")
    return parser.parse_args()


def _run(script: str, argv: list[str]) -> int:
    cmd = [sys.executable, str(ROOT / "scripts" / script), *argv]
    return subprocess.call(cmd)


def main() -> int:
    args = parse_args()
    try:
        source = resolve_monitoramento(args.input)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    as_of = args.as_of or stamp_from_export(source)
    out = Path(args.out).expanduser() if args.out else ROOT / "output" / f"lastmile_{as_of}"

    owner_argv = [
        "--input",
        str(source),
        "--as-of",
        as_of,
        "--out",
        str(out),
        "--region",
        args.region,
    ]
    if args.prazo:
        owner_argv.extend(["--prazo", args.prazo])
    if args.force_ingest:
        owner_argv.append("--force-ingest")

    print(f"Pack {as_of}  fonte: {source.name}", flush=True)
    code = _run("generate_lastmile_by_owner.py", owner_argv)
    if code:
        return code

    if args.skip_tiktok:
        return 0

    parquet = ROOT / "data" / "parquet" / f"monitoramento_{as_of}.parquet"
    tiktok_argv = [
        "--input",
        str(parquet if parquet.is_file() else source),
        "--as-of",
        as_of,
        "--out",
        str(out / "tiktok_sla"),
        "--skip-ingest",
    ]
    if args.prazo:
        tiktok_argv.extend(["--prazo", args.prazo])
    return _run("generate_tiktok_sla.py", tiktok_argv)


if __name__ == "__main__":
    raise SystemExit(main())
