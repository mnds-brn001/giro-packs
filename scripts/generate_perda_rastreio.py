r"""Cobrança direta de perda de rastreio.

Coloque o xlsx em data\monitoramento da perda de rastreio. A carteira sai de
data\prazo. Hub fica no resumo, fora da mensagem da base.

    python scripts\generate_perda_rastreio.py
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
from src.perda_rastreio import (
    CHARGE_MIN_DAYS,
    MIN_BASE,
    build_perda_rastreio,
    load_perda_rastreio,
    resolve_perda_rastreio,
    stamp_from_controle,
)
from src.preventivo_lastmile import load_prazo_tables


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", name).strip(". ")
    return cleaned[:100] or "SEM_BASE"


def _write_excel(routine, destination: Path) -> None:
    frames = {
        "Bases": routine.summary,
        "Parados": routine.grave,
        "Cauda": routine.tail,
        "Hubs": routine.hubs,
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


def _write_named(messages: dict[str, str], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    blocks = []
    for name, message in messages.items():
        (folder / f"{_safe_filename(name)}.txt").write_text(message + "\n", encoding="utf-8")
        blocks.append(f"{'=' * 72}\n{name}\n{'=' * 72}\n{message}")
    if blocks:
        (folder / "TODAS.txt").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cobrança direta de perda de rastreio por base.")
    parser.add_argument(
        "--input",
        default=None,
        help="XLSX de pacotes parados (padrão: o mais novo em data/monitoramento da perda de rastreio).",
    )
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD. Se omitido, usa a data do nome do arquivo.")
    parser.add_argument("--out", default=None, help="Pasta de saída (padrão: output/perda_rastreio_YYYY-MM-DD).")
    parser.add_argument("--prazo", default=None, help="Planilha de prazo (padrão: data/prazo).")
    parser.add_argument("--responsavel", default=None, help="Filtra uma carteira, ex.: Bruno Mendes")
    parser.add_argument("--min-dias", type=int, default=CHARGE_MIN_DAYS, help="Faixa mínima que entra na cobrança.")
    parser.add_argument("--min-base", type=int, default=MIN_BASE, help="Mínimo de AJs para a base virar mensagem.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        source = resolve_perda_rastreio(args.input, root=ROOT)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    stamp = args.as_of or stamp_from_controle(source)
    if not stamp:
        print("ERRO: informe --as-of YYYY-MM-DD. O nome do arquivo nao tem data.", file=sys.stderr)
        return 2
    try:
        corte = pd.Timestamp(stamp).normalize()
    except (TypeError, ValueError):
        print("ERRO: --as-of deve estar no formato YYYY-MM-DD.", file=sys.stderr)
        return 2

    try:
        print("Lendo pacotes parados...", flush=True)
        frame = load_perda_rastreio(source)
        responsabilidade = None
        try:
            prazo_path = resolve_prazo(args.prazo, root=ROOT)
        except FileNotFoundError as exc:
            if args.prazo:
                raise
            print(f"AVISO: {exc}", flush=True)
            prazo_path = None
        if prazo_path is not None:
            _, responsabilidade = load_prazo_tables(prazo_path)
            print(f"  Prazo: {prazo_path.name}", flush=True)
        routine = build_perda_rastreio(
            frame,
            as_of=corte,
            min_days=args.min_dias,
            min_base=args.min_base,
            responsavel=args.responsavel,
            responsabilidade=responsabilidade,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out).expanduser() if args.out else ROOT / "output" / f"perda_rastreio_{corte.strftime('%Y-%m-%d')}"
    out.mkdir(parents=True, exist_ok=True)
    excel = out / f"perda_rastreio_{corte.strftime('%Y-%m-%d')}.xlsx"
    _write_excel(routine, excel)
    _write_named(routine.messages, out / "mensagens")
    _write_named(routine.owner_messages, out / "por_responsavel")
    _write_named(routine.hub_messages, out / "hubs")
    n_msg = int(routine.grave["ponto"].isin(routine.messages).sum()) if not routine.grave.empty else 0
    index = [
        f"Perda de rastreio {corte.strftime('%d/%m/%Y')}",
        f"Fonte: {source.name}",
        f"Pacotes no controle: {len(routine.detail)}",
        f"Na base (>={args.min_dias}d): {len(routine.grave)}",
        f"No hub: {int(routine.detail['is_hub'].sum()) if 'is_hub' in routine.detail.columns else 0}",
        f"Mensagens de base: {len(routine.messages)} bases, {n_msg} AJs",
        f"Cauda: {int(routine.tail['parados'].sum()) if not routine.tail.empty else 0} AJs em {len(routine.tail)} bases",
        "",
        routine.summary.to_string(index=False),
    ]
    (out / "INDEX.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"OK: {out}")
    print(f"  Controle: {len(routine.detail)}  base: {len(routine.grave)}  hub: {len(routine.hubs)}  msgs: {len(routine.messages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
