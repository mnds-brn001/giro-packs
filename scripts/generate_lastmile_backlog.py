r"""Gera o pacote diário de backlog e exceções de SLA por base.

Exemplo (xlsx em data/monitoramento):
    python scripts/generate_lastmile_backlog.py ^
      --as-of 2026-08-06 --out output\lastmile_2026-08-06
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

from src.backlog_lastmile import LastMileRoutine, build_lastmile_routine, load_lastmile_source
from src.ingest import ingest_monitoramento, resolve_monitoramento, resolve_prazo
from src.preventivo_lastmile import bases_for_owner, load_prazo_tables


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", name).strip(". ")
    return cleaned[:100] or "SEM_BASE"


def _write_excel(routine: LastMileRoutine, destination: Path, *, include_detail: bool = True) -> None:
    frames = {
        "Resumo por Base": routine.base_summary,
        "Vence Hoje - Em Rota": routine.due_today_route,
        "No Piso - Vence Hoje": routine.due_today_floor,
    }
    if include_detail:
        frames["Backlog Regional"] = routine.regional_backlog
        delay = pd.to_numeric(routine.regional_backlog.get("delay_days"), errors="coerce")
        if delay is not None and not routine.regional_backlog.empty:
            critico = routine.regional_backlog.loc[delay.fillna(0) > 10]
            if not critico.empty:
                frames["Critico +10"] = critico

    with pd.ExcelWriter(destination, engine="xlsxwriter", datetime_format="dd/mm/yyyy hh:mm") as writer:
        workbook = writer.book
        title_format = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#073B18", "border": 0}
        )
        alert_format = workbook.add_format({"font_color": "#C00000", "bold": True})
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
            worksheet = writer.sheets[sheet]
            worksheet.freeze_panes(1, 0)
            if not frame.empty and len(frame) <= 50_000:
                worksheet.autofilter(0, 0, len(frame), max(len(frame.columns) - 1, 0))
            for col_idx, column in enumerate(frame.columns):
                # Largura pelo cabeçalho apenas — medir cada célula em abas
                # grandes (centenas de mil linhas) custa minutos.
                width = min(max(len(str(column)) + 2, 14), 36)
                worksheet.set_column(col_idx, col_idx, width)
                worksheet.write(0, col_idx, column, title_format)
            if sheet == "Vence Hoje - Em Rota" and not frame.empty:
                worksheet.set_column(0, 0, 24, alert_format)


def _write_messages(messages: dict[str, str], messages_dir: Path) -> None:
    messages_dir.mkdir(parents=True, exist_ok=True)
    all_messages: list[str] = []
    for base, message in messages.items():
        (messages_dir / f"{_safe_filename(base)}.txt").write_text(message, encoding="utf-8")
        all_messages.append(f"{'=' * 72}\n{base}\n{'=' * 72}\n{message}")
    (messages_dir / "TODAS_AS_BASES.txt").write_text("\n\n".join(all_messages), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consolida backlog Last Mile e exceções de SLA para envio às bases."
    )
    parser.add_argument(
        "--input",
        default=None,
        help="XLSX/Parquet da Tabela Mestra (padrão: data/monitoramento ou raiz).",
    )
    parser.add_argument(
        "--out",
        default="output/lastmile",
        help="Pasta onde serão criados o Excel e as mensagens (padrão: output/lastmile).",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Data de referência YYYY-MM-DD (padrão: hoje).",
    )
    parser.add_argument(
        "--region",
        default="Regional Sul",
        help="Nome exato da região no Excel; use '' para não filtrar (padrão: Regional Sul).",
    )
    parser.add_argument(
        "--inbound-from",
        default=None,
        help="Início da janela pelo Tempo de inbound no ponto (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--inbound-to",
        default=None,
        help="Fim da janela pelo Tempo de inbound no ponto (YYYY-MM-DD ou YYYY-MM-DD HH:MM).",
    )
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Não grava a aba Backlog Regional (mais rápido em exports grandes).",
    )
    parser.add_argument(
        "--prazo",
        default=None,
        help="Prazo Lastmille e Responsabilidade.xlsx — usado só para filtrar a carteira. "
        "Se omitido com --responsavel, procura em data/prazo.",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Não grava Parquet da torre.",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        help="Reconverte o XLSX mesmo se o Parquet já estiver atualizado.",
    )
    parser.add_argument(
        "--responsavel",
        default=None,
        help="Filtra as bases da carteira (ex.: 'Bruno Mendes'). Exige --prazo.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.skip_ingest:
            source = resolve_monitoramento(args.input)
        else:
            print("Ingerindo monitoramento -> Parquet da torre...", flush=True)
            ingest = ingest_monitoramento(args.input, force=args.force_ingest)
            source = ingest.parquet
            verb = "já atualizado" if ingest.skipped else "convertido"
            print(f"  {verb}: {ingest.parquet.name}", flush=True)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    try:
        reference = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp.now()
        inbound_from = pd.Timestamp(args.inbound_from) if args.inbound_from else None
        inbound_to = pd.Timestamp(args.inbound_to) if args.inbound_to else None
    except (TypeError, ValueError):
        print("ERRO: datas devem estar no formato YYYY-MM-DD.", file=sys.stderr)
        return 2

    try:
        print("Lendo extracao (modo rapido, colunas Last Mile)...", flush=True)
        tracking = load_lastmile_source(source, as_of=reference)
        print(f"  Linhas lidas: {len(tracking):,}".replace(",", "."), flush=True)
        only_bases = None
        if args.responsavel:
            try:
                prazo_path = resolve_prazo(args.prazo)
            except FileNotFoundError as exc:
                print(f"ERRO: {exc}", file=sys.stderr)
                return 2
            _, responsabilidade = load_prazo_tables(prazo_path)
            only_bases = bases_for_owner(responsabilidade, args.responsavel)
            if not only_bases:
                print(f"ERRO: nenhuma base para responsável {args.responsavel!r}.", file=sys.stderr)
                return 2
            print(f"  Carteira {args.responsavel}: {len(only_bases)} bases", flush=True)
        routine = build_lastmile_routine(
            tracking,
            as_of=reference,
            region=args.region or None,
            inbound_from=inbound_from,
            inbound_to=inbound_to,
            only_bases=only_bases,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERRO ao processar a tabela mestra: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.out).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    date_stamp = routine.as_of.strftime("%Y-%m-%d")
    excel_path = output_dir / f"backlog_lastmile_{date_stamp}.xlsx"
    print("Gerando Excel e mensagens...", flush=True)
    _write_excel(routine, excel_path, include_detail=not args.no_detail)
    _write_messages(routine.messages, output_dir / "mensagens_whatsapp")

    print(f"OK: {excel_path}")
    print(f"  Backlog regional: {len(routine.regional_backlog)}")
    print(f"  Crítico +10d: {int((pd.to_numeric(routine.regional_backlog.get('delay_days'), errors='coerce').fillna(0) > 10).sum()) if not routine.regional_backlog.empty else 0}")
    print(f"  Mensagens backlog: {len(routine.messages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
