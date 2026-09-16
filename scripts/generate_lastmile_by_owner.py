r"""Gera backlog + preventivo (inbound+D0/D+N) em pasta por responsável.

Também grava ``data/parquet/monitoramento_YYYY-MM-DD.parquet`` e
``monitoramento_latest.parquet`` para a torre Streamlit.

Exemplo (xlsx em data/monitoramento e prazo em data/prazo):
    python scripts/generate_lastmile_by_owner.py ^
      --as-of 2026-09-08 --out output\lastmile_2026-09-08

Ou com caminhos explícitos:
    python scripts/generate_lastmile_by_owner.py ^
      --input monitoramento.xlsx --prazo prazo.xlsx ^
      --as-of 2026-08-31 --out output\lastmile_2026-08-31
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backlog_lastmile import build_lastmile_routine, load_lastmile_source
from src.ingest import ingest_monitoramento, resolve_monitoramento, resolve_prazo
from src.mesh_action import PRIORIDADE_CRITICA, build_mesh_action, write_mesh_excel
from src.preventivo_lastmile import build_preventivo_routine, load_prazo_tables


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE)
    return cleaned.strip("_")[:80] or "SEM_RESPONSAVEL"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Packs Last Mile por responsável.")
    parser.add_argument(
        "--input",
        default=None,
        help="XLSX/Parquet da Tabela Mestra (padrão: data/monitoramento ou raiz).",
    )
    parser.add_argument(
        "--prazo",
        default=None,
        help="Prazo Lastmille e Responsabilidade.xlsx (padrão: data/prazo ou raiz).",
    )
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--out", default="output/lastmile")
    parser.add_argument("--region", default="Regional Sul")
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Não grava Parquet da torre (só gera os packs).",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        help="Reconverte o XLSX mesmo se o Parquet já estiver atualizado.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        prazo_path = resolve_prazo(args.prazo)
        if args.skip_ingest:
            source = resolve_monitoramento(args.input)
            ingest = None
        else:
            print("Ingerindo monitoramento -> Parquet da torre...", flush=True)
            ingest = ingest_monitoramento(args.input, force=args.force_ingest)
            source = ingest.parquet
            verb = "já atualizado" if ingest.skipped else "convertido"
            print(f"  {verb}: {ingest.parquet.name}  (torre: {ingest.latest.name})", flush=True)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    try:
        reference = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp.now()
    except (TypeError, ValueError):
        print("ERRO: --as-of YYYY-MM-DD", file=sys.stderr)
        return 2

    backlog_mod = _load_script("generate_lastmile_backlog.py")
    preventivo_mod = _load_script("generate_lastmile_preventivo.py")

    print("Lendo extracao...", flush=True)
    tracking = load_lastmile_source(source, as_of=reference)
    print(f"  Linhas: {len(tracking):,}".replace(",", "."), flush=True)
    print("Lendo prazo + carteira...", flush=True)
    prazo_lookup, responsabilidade = load_prazo_tables(prazo_path)
    owners = sorted(
        {str(x).strip() for x in responsabilidade["responsavel"].dropna() if str(x).strip()}
    )
    print(f"  Responsaveis: {len(owners)}", flush=True)

    out_root = Path(args.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp(reference).strftime("%Y-%m-%d")
    index_lines = [
        f"Last Mile {stamp} — packs por responsavel",
        f"Fonte: {ingest.source.name if ingest else source.name}",
        f"Prazo: {prazo_path.name}",
        f"Parquet torre: {ingest.latest if ingest else '—'}",
        f"Linhas na extracao: {len(tracking)}",
        "",
        f"{'Responsavel':<24} {'Bases':>6} {'Backlog':>8} {'+10d':>6} {'VENCE HOJE':>10} {'Malha':>6}",
    ]

    print("Montando painel de acao de malha...", flush=True)
    malha = build_mesh_action(
        tracking,
        as_of=reference,
        responsabilidade=responsabilidade,
        region=args.region or None,
    )
    write_mesh_excel(malha, out_root / "malha_acao" / f"painel_malha_{stamp}.xlsx")
    n_malha = len(malha)
    n_malha_crit = int(malha["Prioridade"].eq(PRIORIDADE_CRITICA).sum()) if n_malha else 0
    print(f"  Desvios: {n_malha}  (critica: {n_malha_crit})", flush=True)

    for owner in owners:
        bases = sorted(
            responsabilidade.loc[
                responsabilidade["responsavel"].str.contains(owner, case=False, na=False),
                "base",
            ]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
        )
        folder = out_root / _slug(owner)
        folder.mkdir(parents=True, exist_ok=True)
        print(f"  {owner} ({len(bases)} bases)...", flush=True)

        routine = build_lastmile_routine(
            tracking,
            as_of=reference,
            region=args.region or None,
            only_bases=bases,
        )
        n_crit = 0
        if not routine.regional_backlog.empty:
            delay = pd.to_numeric(routine.regional_backlog.get("delay_days"), errors="coerce").fillna(0)
            n_crit = int((delay > 10).sum())
        backlog_mod._write_excel(routine, folder / f"backlog_lastmile_{stamp}.xlsx")
        backlog_mod._write_messages(routine.messages, folder / "mensagens_whatsapp")

        prev = build_preventivo_routine(
            tracking,
            as_of=reference,
            prazo_lookup=prazo_lookup,
            only_bases=bases,
        )
        preventivo_mod._write_excel(prev, folder / f"preventivo_lastmile_{stamp}.xlsx")
        preventivo_mod._write_messages(prev.messages, folder / "mensagens_preventivo")

        owner_malha = (
            malha.loc[malha["Responsável"].astype("string").str.contains(owner, case=False, na=False)]
            if n_malha and "Responsável" in malha.columns
            else malha.iloc[0:0]
        )
        write_mesh_excel(owner_malha, folder / f"malha_acao_{stamp}.xlsx")

        index_lines.append(
            f"{owner:<24} {len(bases):>6} {len(routine.regional_backlog):>8} {n_crit:>6} {len(prev.vence_hoje):>10} {len(owner_malha):>6}"
        )

    (out_root / "INDEX.txt").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    print(f"OK: {out_root / 'INDEX.txt'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
