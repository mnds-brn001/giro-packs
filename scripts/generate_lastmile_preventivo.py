r"""Gera o preventivo Last Mile: RESUMO da matriz + copy de vencendo hoje.

Exemplo:
    python scripts/generate_lastmile_preventivo.py ^
      --bd "C:\Users\user\Downloads\Preventivo Lastmile -- 13.08.2026.xlsx" ^
      --hq "C:\Users\user\Downloads\PREVENTIVO LM - 13.08 - SUL.xlsx" ^
      --as-of 2026-08-13 --out output\lastmile_2026-08-13
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

from src.ingest import resolve_prazo
from src.preventivo_lastmile import (
    RESUMO_VENCE_HOJE,
    PreventivoRoutine,
    bases_for_owner,
    build_preventivo_routine,
    load_hq_resumo,
    load_prazo_tables,
    load_preventivo_bd,
)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", name).strip(". ")
    return cleaned[:100] or "SEM_BASE"


def _write_excel(routine: PreventivoRoutine, destination: Path) -> None:
    vence_cols = [
        c
        for c in ("base", "waybill", "status", "courier", "dest_city", "last_track", "resumo")
        if c in routine.vence_hoje.columns
    ]
    frames = {
        "Dinamica RESUMO": routine.pivot,
        "Vence Hoje": routine.vence_hoje[vence_cols] if vence_cols else routine.vence_hoje,
        "BD + RESUMO": routine.detail,
    }
    with pd.ExcelWriter(destination, engine="xlsxwriter", datetime_format="dd/mm/yyyy hh:mm") as writer:
        workbook = writer.book
        title_format = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#073B18"}
        )
        alert_format = workbook.add_format({"font_color": "#C00000", "bold": True})
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
            worksheet = writer.sheets[sheet]
            worksheet.freeze_panes(1, 0)
            if not frame.empty:
                worksheet.autofilter(0, 0, len(frame), max(len(frame.columns) - 1, 0))
            for col_idx, column in enumerate(frame.columns):
                width = min(max(len(str(column)) + 2, 14), 42)
                worksheet.set_column(col_idx, col_idx, width)
                worksheet.write(0, col_idx, column, title_format)
            if sheet == "Vence Hoje" and RESUMO_VENCE_HOJE in frame.columns:
                worksheet.set_column(0, 0, 18, alert_format)


def _write_messages(messages: dict[str, str], messages_dir: Path) -> None:
    messages_dir.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for base, message in messages.items():
        (messages_dir / f"{_safe_filename(base)}.txt").write_text(message, encoding="utf-8")
        blocks.append(f"{'=' * 72}\n{base}\n{'=' * 72}\n{message}")
    (messages_dir / "TODAS_AS_BASES.txt").write_text("\n\n".join(blocks), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preventivo Last Mile: PROCV do RESUMO da matriz + copy de VENCE HOJE."
    )
    parser.add_argument(
        "--bd",
        required=True,
        help="XLSX operacional (Preventivo Lastmile) com a aba BD atualizada.",
    )
    parser.add_argument(
        "--hq",
        default=None,
        help="XLSX PREVENTIVO LM da matriz (Detalhes1/BD, coluna Resumo Prazo). Opcional se BD já tiver RESUMO.",
    )
    parser.add_argument(
        "--prazo",
        default=None,
        help="Prazo Lastmille e Responsabilidade.xlsx — calcula RESUMO por inbound + D0/D+N.",
    )
    parser.add_argument(
        "--responsavel",
        default=None,
        help="Filtra as bases da carteira (ex.: 'Bruno Mendes'). Exige --prazo.",
    )
    parser.add_argument("--as-of", default=None, help="Data de referência YYYY-MM-DD.")
    parser.add_argument("--out", default="output/lastmile", help="Pasta de saída.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bd_path = Path(args.bd).expanduser()
    if not bd_path.is_file():
        print(f"ERRO: BD não encontrado: {bd_path}", file=sys.stderr)
        return 2
    try:
        reference = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp.now()
    except (TypeError, ValueError):
        print("ERRO: --as-of deve estar no formato YYYY-MM-DD.", file=sys.stderr)
        return 2

    try:
        print("Lendo extração operacional…", flush=True)
        bd = load_preventivo_bd(bd_path)
        hq_resumo = None
        prazo_lookup = None
        only_bases = None
        if args.prazo:
            try:
                prazo_path = resolve_prazo(args.prazo)
            except FileNotFoundError as exc:
                print(f"ERRO: {exc}", file=sys.stderr)
                return 2
            print("Calculando RESUMO por inbound + prazo D0/D+N…", flush=True)
            prazo_lookup, responsabilidade = load_prazo_tables(prazo_path)
            if args.responsavel:
                only_bases = bases_for_owner(responsabilidade, args.responsavel)
                if not only_bases:
                    print(f"ERRO: nenhuma base para responsável {args.responsavel!r}.", file=sys.stderr)
                    return 2
                print(f"  Carteira {args.responsavel}: {len(only_bases)} bases", flush=True)
        if args.hq:
            hq_path = Path(args.hq).expanduser()
            if not hq_path.is_file():
                print(f"ERRO: HQ não encontrado: {hq_path}", file=sys.stderr)
                return 2
            print(f"Lendo RESUMO da matriz {hq_path.name}…", flush=True)
            hq_resumo = load_hq_resumo(hq_path)
        routine = build_preventivo_routine(
            bd,
            as_of=reference,
            hq_resumo=hq_resumo,
            prazo_lookup=prazo_lookup,
            only_bases=only_bases,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERRO ao processar o preventivo: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.out).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    excel_path = output_dir / f"preventivo_lastmile_{routine.as_of.strftime('%Y-%m-%d')}.xlsx"
    _write_excel(routine, excel_path)
    _write_messages(routine.messages, output_dir / "mensagens_preventivo")

    print(f"OK: {excel_path}")
    print(f"  Linhas BD: {len(routine.detail)}")
    print(f"  VENCE HOJE: {len(routine.vence_hoje)}")
    print(f"  Mensagens: {len(routine.messages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
