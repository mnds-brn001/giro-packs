r"""Cobrança cirúrgica TikTok Logistics — SLA last mile (inbound + D0/D+N)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.columns import COL
from src.ingest import ingest_monitoramento, resolve_monitoramento, resolve_prazo
from src.preventivo_lastmile import (
    RESUMO_DEPOIS,
    RESUMO_FORA,
    RESUMO_VENCE_HOJE,
    apply_inbound_resumo,
    load_prazo_tables,
)
from src.tiktok_sla import closed_loop_due_today, tiktok_ciclo_message, tiktok_message

CLIENT_NEEDLE = "tiktok"
KEYS = (
    "waybill",
    "status",
    "due_at",
    "base_inbound",
    "last_point",
    "courier",
    "last_track",
    "issue_reason",
    "delay_days",
    "dest_city",
    "dest_state",
    "client",
    "merchant",
    "closed_loop_at",
)


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", str(name).strip(), flags=re.UNICODE)
    return cleaned.strip("_")[:80] or "SEM"


def _load_tiktok(path: Path) -> pd.DataFrame:
    names = {COL[k] for k in KEYS if k in COL}
    if path.suffix.lower() in {".parquet", ".pq"}:
        raw = pd.read_parquet(path)
        bilingual = [c for c in raw.columns if c in names]
        if bilingual:
            raw = raw[bilingual]
        else:
            keep = [c for c in raw.columns if c in KEYS]
            if keep:
                raw = raw[keep]
    else:
        raw = pd.read_excel(path, engine="calamine", usecols=lambda c: c in names)
    work = raw.rename(columns={COL[k]: k for k in KEYS if k in COL})
    for col in ("due_at", "base_inbound", "closed_loop_at"):
        if col in work.columns:
            work[col] = pd.to_datetime(work[col], errors="coerce")
    if "delay_days" in work.columns:
        work["delay_days"] = pd.to_numeric(work["delay_days"], errors="coerce")
    client = work.get("client", pd.Series("", index=work.index)).astype("string")
    merchant = work.get("merchant", pd.Series("", index=work.index)).astype("string")
    mask = client.str.contains(CLIENT_NEEDLE, case=False, na=False) | merchant.str.contains(
        CLIENT_NEEDLE, case=False, na=False
    )
    return work.loc[mask].copy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy TikTok SLA por base.")
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
    parser.add_argument("--as-of", default="2026-08-31")
    parser.add_argument("--out", default="output/tiktok_sla")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--force-ingest", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        prazo_path = resolve_prazo(args.prazo)
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
    as_of = pd.Timestamp(args.as_of).normalize()

    print("Lendo TikTok na extracao...", flush=True)
    tt = _load_tiktok(source)
    print(f"  Pacotes TikTok: {len(tt)}", flush=True)
    lookup, resp = load_prazo_tables(prazo_path)
    tt["base"] = tt["last_point"].astype("string").str.strip()
    resp = resp.copy()
    resp["base"] = resp["base"].astype("string").str.strip()
    tt = tt.merge(resp, on="base", how="left")
    tt["responsavel"] = tt["responsavel"].fillna("SEM CARTEIRA").astype("string").str.strip()
    tt = apply_inbound_resumo(tt, lookup, as_of)
    venc = pd.to_datetime(tt["lm_vencimento"], errors="coerce").dt.normalize()
    tt["dias_fora"] = (as_of - venc).dt.days
    tt["dias_fora"] = tt["dias_fora"].where(tt["resumo"].eq(RESUMO_FORA), 0)
    if "closed_loop_at" not in tt.columns:
        tt["closed_loop_at"] = pd.NaT
    tt["ciclo_hoje"] = ""
    tt.loc[closed_loop_due_today(tt["closed_loop_at"], as_of), "ciclo_hoje"] = "sim"

    acao = tt.loc[tt["resumo"].isin([RESUMO_FORA, RESUMO_VENCE_HOJE, RESUMO_DEPOIS])].copy()
    out = Path(args.out).expanduser()
    zap = out / "mensagens_whatsapp"
    zap.mkdir(parents=True, exist_ok=True)
    owners_dir = out / "por_responsavel"
    owners_dir.mkdir(parents=True, exist_ok=True)

    messages: dict[str, str] = {}
    owner_blocks: dict[str, list[str]] = {}
    for base, group in tt.groupby("base", dropna=False, sort=True):
        if group["resumo"].isin([RESUMO_FORA, RESUMO_VENCE_HOJE]).sum() == 0:
            continue
        msg = tiktok_message(str(base), group, as_of)
        messages[str(base)] = msg
        (zap / f"{_slug(base)}.txt").write_text(msg, encoding="utf-8")
        owner = str(group["responsavel"].iloc[0])
        owner_blocks.setdefault(owner, []).append(f"{'=' * 64}\n{base}\n{'=' * 64}\n{msg}")

    all_txt = []
    for owner in sorted(owner_blocks):
        body = "\n\n".join(owner_blocks[owner])
        (owners_dir / f"{_slug(owner)}.txt").write_text(body, encoding="utf-8")
        all_txt.append(body)
    (zap / "TODAS_AS_BASES.txt").write_text("\n\n".join(all_txt), encoding="utf-8")

    ciclo_dir = out / "ciclo_fechado"
    ciclo_dir.mkdir(parents=True, exist_ok=True)
    ciclo_owners = ciclo_dir / "por_responsavel"
    ciclo_owners.mkdir(parents=True, exist_ok=True)
    ciclo_blocks: dict[str, list[str]] = {}
    n_ciclo_msgs = 0
    ciclo = tt.loc[tt["ciclo_hoje"].eq("sim")]
    for base, group in ciclo.groupby("base", dropna=False, sort=True):
        msg = tiktok_ciclo_message(str(base), group, as_of)
        n_ciclo_msgs += 1
        (ciclo_dir / f"{_slug(base)}.txt").write_text(msg, encoding="utf-8")
        owner = str(group["responsavel"].iloc[0])
        ciclo_blocks.setdefault(owner, []).append(f"{'=' * 64}\n{base}\n{'=' * 64}\n{msg}")
    ciclo_all = []
    for owner in sorted(ciclo_blocks):
        body = "\n\n".join(ciclo_blocks[owner])
        (ciclo_owners / f"{_slug(owner)}.txt").write_text(body, encoding="utf-8")
        ciclo_all.append(body)
    (ciclo_dir / "TODAS_AS_BASES.txt").write_text("\n\n".join(ciclo_all), encoding="utf-8")

    resumo = (
        tt.groupby(["responsavel", "base"], dropna=False)
        .agg(
            tiktok=("waybill", "size"),
            fora=("resumo", lambda s: int(s.eq(RESUMO_FORA).sum())),
            vence_hoje=("resumo", lambda s: int(s.eq(RESUMO_VENCE_HOJE).sum())),
            vence_depois=("resumo", lambda s: int(s.eq(RESUMO_DEPOIS).sum())),
        )
        .reset_index()
        .sort_values(["fora", "vence_hoje"], ascending=[False, False])
    )
    detalhe = acao.sort_values(["resumo", "dias_fora", "base", "waybill"], ascending=[True, False, True, True])
    front = [
        "waybill",
        "responsavel",
        "base",
        "status",
        "resumo",
        "dias_fora",
        "closed_loop_at",
        "ciclo_hoje",
        "issue_reason",
        "base_inbound",
        "dest_city",
        "courier",
        "lm_dias",
        "corte_8h",
        "lm_vencimento",
        "last_track",
    ]
    ordered = [c for c in front if c in detalhe.columns] + [c for c in detalhe.columns if c not in front]
    detalhe = detalhe[ordered]
    ciclo_sheet = detalhe.loc[detalhe["ciclo_hoje"].eq("sim")] if "ciclo_hoje" in detalhe.columns else detalhe.iloc[0:0]
    excel = out / f"tiktok_sla_{as_of.strftime('%Y-%m-%d')}.xlsx"
    with pd.ExcelWriter(excel, engine="xlsxwriter", datetime_format="dd/mm/yyyy hh:mm") as writer:
        resumo.to_excel(writer, sheet_name="Resumo bases", index=False)
        detalhe.to_excel(writer, sheet_name="Pedidos", index=False)
        ciclo_sheet.to_excel(writer, sheet_name="Ciclo fecha hoje", index=False)

    n_fora = int(tt["resumo"].eq(RESUMO_FORA).sum())
    n_hoje = int(tt["resumo"].eq(RESUMO_VENCE_HOJE).sum())
    index = [
        f"TikTok SLA {as_of.strftime('%d/%m/%Y')}",
        f"Cliente: tiktok logistics brazil ltda",
        f"Pacotes na extracao: {len(tt)}",
        f"FORA (inbound+prazo): {n_fora}",
        f"VENCE HOJE: {n_hoje}",
        f"VENCE DEPOIS: {int(tt['resumo'].eq(RESUMO_DEPOIS).sum())}",
        f"Ciclo fecha hoje: {len(ciclo)}",
        f"Bases com cobranca: {len(messages)}",
        f"Blocos de ciclo: {n_ciclo_msgs}",
        f"Excel: {excel.name}",
        "",
        resumo.head(15).to_string(index=False),
    ]
    (out / "INDEX.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"OK: {out}", flush=True)
    print(
        f"  TikTok: {len(tt)}  FORA: {n_fora}  VENCE HOJE: {n_hoje}  "
        f"ciclo hoje: {len(ciclo)}  msgs: {len(messages)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
