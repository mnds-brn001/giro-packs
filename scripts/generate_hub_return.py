r"""Gera cobrança de devolução ao hub superior (txt por base + HTML).

Exemplo:
    python scripts/generate_hub_return.py ^
      --input "C:\Users\user\Downloads\Acompanhamento Devolução ao Hub Superior 2608_1900.xlsx" ^
      --as-of 2026-08-26 --out output\hub_return_2026-08-26
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.hub_return import (
    BUCKET_ORANGE,
    BUCKET_RED,
    HubReturnRoutine,
    build_hub_return_routine,
    load_hub_return_source,
)
from src.preventivo_lastmile import bases_for_owner, load_prazo_tables


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", name).strip(". ")
    return cleaned[:100] or "SEM_BASE"


def _write_excel(routine: HubReturnRoutine, destination: Path) -> None:
    frames = {
        "Resumo por Base": routine.summary,
        "Criticos +7d": routine.detail.loc[
            routine.detail["bucket"].isin({BUCKET_RED, BUCKET_ORANGE})
        ],
        "Detalhe": routine.detail,
    }
    with pd.ExcelWriter(destination, engine="xlsxwriter") as writer:
        book = writer.book
        header = book.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#073B18"})
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            ws.freeze_panes(1, 0)
            if not frame.empty:
                ws.autofilter(0, 0, len(frame), max(len(frame.columns) - 1, 0))
            for idx, col in enumerate(frame.columns):
                ws.set_column(idx, idx, min(max(len(str(col)) + 2, 14), 42))
                ws.write(0, idx, col, header)


def _write_messages(messages: dict[str, str], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    blocks = []
    for base, message in messages.items():
        (folder / f"{_safe_filename(base)}.txt").write_text(message, encoding="utf-8")
        blocks.append(f"{'=' * 72}\n{base}\n{'=' * 72}\n{message}")
    (folder / "TODAS_AS_BASES.txt").write_text("\n\n".join(blocks), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copies de devolução ao hub superior.")
    parser.add_argument("--input", required=True, help="XLSX Acompanhamento Devolução ao Hub Superior.")
    parser.add_argument("--as-of", default=None, help="Data-base YYYY-MM-DD (padrão: hoje).")
    parser.add_argument("--out", default="output/hub_return", help="Pasta de saída.")
    parser.add_argument("--prazo", default=None, help="Prazo Lastmille e Responsabilidade.xlsx")
    parser.add_argument("--responsavel", default=None, help="Filtra carteira, ex.: Bruno Mendes")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.input).expanduser()
    if not source.is_file():
        print(f"ERRO: arquivo não encontrado: {source}", file=sys.stderr)
        return 2
    try:
        reference = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp.now()
    except (TypeError, ValueError):
        print("ERRO: --as-of deve estar no formato YYYY-MM-DD.", file=sys.stderr)
        return 2

    try:
        print("Lendo acompanhamento de devolução ao hub…", flush=True)
        df = load_hub_return_source(source)
        only_bases = None
        if args.prazo and args.responsavel:
            prazo_path = Path(args.prazo).expanduser()
            _, resp = load_prazo_tables(prazo_path)
            only_bases = bases_for_owner(resp, args.responsavel)
            print(f"  Carteira {args.responsavel}: {len(only_bases)} bases", flush=True)
        routine = build_hub_return_routine(df, as_of=reference, only_bases=only_bases)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = routine.as_of.strftime("%Y-%m-%d")
    excel = out / f"hub_return_{stamp}.xlsx"
    html_path = out / f"hub_return_{stamp}.html"
    _write_excel(routine, excel)
    _write_messages(routine.messages, out / "mensagens_whatsapp")
    html_path.write_text(routine.html, encoding="utf-8")
    (out / "index.html").write_text(routine.html, encoding="utf-8")

    n_red = int((routine.detail["bucket"] == BUCKET_RED).sum())
    print(f"OK: {excel}")
    print(f"  Pacotes: {len(routine.detail)}")
    print(f"  +10d: {n_red}")
    print(f"  Mensagens: {len(routine.messages)}")
    print(f"  HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
