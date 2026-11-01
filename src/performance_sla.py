"""Performance SLA do dia: o que vencia hoje, no prazo, em aberto ou fora.

O recorte é o vencimento last-mile (inbound + TIME DELIVERY DIAS, com o corte
das 08:00 no D0). A assinatura fecha DENTRO ou FORA. Sem assinatura, fica
EM ABERTO. A carteira traz o responsável e o tipo de operação da planilha nova.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .columns import COL
from .preventivo_lastmile import apply_inbound_resumo

SIT_DENTRO = "ENTREGUE DENTRO DO PRAZO / 按时交付"
SIT_ABERTO = "EM ABERTO / 待签收"
SIT_FORA = "ENTREGUE FORA DO PRAZO / 逾期送达"
SIT_SEM_PRAZO = "SEM PRAZO"

PERF_KEYS = (
    "waybill",
    "status",
    "region",
    "dest_state",
    "dest_city",
    "pre_point",
    "entry_point",
    "delivery_point",
    "last_point",
    "courier",
    "base_inbound",
    "signed_at",
)

_EMPTY = {"", "nan", "None", "<NA>"}


def load_tracking_slice(path: str | Path) -> pd.DataFrame:
    """Lê só as colunas do relógio last-mile, de parquet ou xlsx."""
    source = Path(path)
    wanted = {key: COL[key] for key in PERF_KEYS}
    if source.suffix.lower() in {".parquet", ".pq"}:
        names = set(pq.ParquetFile(source).schema.names)
        bilingual = [label for label in wanted.values() if label in names]
        internal = [key for key in wanted if key in names]
        if bilingual:
            raw = pd.read_parquet(source, columns=bilingual)
            raw = raw.rename(columns={label: key for key, label in wanted.items()})
        elif internal:
            raw = pd.read_parquet(source, columns=internal)
        else:
            raw = pd.read_parquet(source)
            raw = raw.rename(columns={label: key for key, label in wanted.items() if label in raw.columns})
    else:
        names = set(wanted.values())
        raw = pd.read_excel(source, engine="calamine", usecols=lambda c: c in names)
        raw = raw.rename(columns={label: key for key, label in wanted.items()})
    for col in ("base_inbound", "signed_at"):
        if col in raw.columns:
            raw[col] = pd.to_datetime(raw[col], errors="coerce")
    if "waybill" in raw.columns:
        raw["waybill"] = raw["waybill"].astype("string").str.strip()
    return raw


def _clean(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return text.mask(text.isin(_EMPTY))


def dashboard_base(work: pd.DataFrame) -> pd.Series:
    """Ponto que aparece na DIN: entrega, senão o último bip, senão a entrada."""
    base = pd.Series(pd.NA, index=work.index, dtype="string")
    for col in ("delivery_point", "last_point", "entry_point", "pre_point"):
        if col not in work.columns:
            continue
        base = base.fillna(_clean(work[col]))
    return base


def _attach_carteira(work: pd.DataFrame, responsabilidade: pd.DataFrame) -> pd.DataFrame:
    out = work.copy()
    out["base"] = dashboard_base(out)
    if responsabilidade is None or responsabilidade.empty:
        out["responsavel"] = "SEM CARTEIRA"
        out["tipo_operacao"] = pd.NA
        return out
    keep = ["base", "responsavel"]
    for col in ("tipo_operacao", "uf", "sac"):
        if col in responsabilidade.columns:
            keep.append(col)
    resp = responsabilidade.loc[:, keep].copy()
    resp["base"] = _clean(resp["base"])
    resp = resp.dropna(subset=["base"]).drop_duplicates("base", keep="first")
    out = out.merge(resp, on="base", how="left")
    out["responsavel"] = out["responsavel"].fillna("SEM CARTEIRA")
    if "tipo_operacao" not in out.columns:
        out["tipo_operacao"] = pd.NA
    return out


def _stack_sources(
    entregues: pd.DataFrame | None,
    abertos: pd.DataFrame | None,
    *,
    region: str | None,
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    if entregues is not None and not entregues.empty:
        signed = entregues.copy()
        signed["_origem"] = "entregue"
        parts.append(signed)
    if abertos is not None and not abertos.empty:
        opened = abertos.copy()
        opened["_origem"] = "aberto"
        parts.append(opened)
    if not parts:
        return pd.DataFrame()
    work = pd.concat(parts, ignore_index=True)
    if region and "region" in work.columns:
        work = work.loc[work["region"].astype("string").str.strip().eq(region)].copy()
    if work.empty or "waybill" not in work.columns:
        return work
    signed_at = pd.to_datetime(work.get("signed_at"), errors="coerce")
    work = work.assign(_has_sign=signed_at.notna().astype(int))
    work = work.sort_values("_has_sign").drop_duplicates("waybill", keep="last")
    return work.drop(columns=["_has_sign"])


def classify_sla(
    entregues: pd.DataFrame | None,
    abertos: pd.DataFrame | None,
    prazo_lookup: pd.DataFrame,
    responsabilidade: pd.DataFrame,
    dia: pd.Timestamp,
    *,
    region: str | None = "Regional Sul",
) -> pd.DataFrame:
    """Toda a extração, com situação e a marca de quem vence em ``dia``."""
    work = _stack_sources(entregues, abertos, region=region)
    if work.empty:
        return work
    stamped = apply_inbound_resumo(work, prazo_lookup, pd.Timestamp(dia))
    stamped = _attach_carteira(stamped, responsabilidade)
    known = stamped["lm_dias"].notna()
    venc = pd.to_datetime(stamped["lm_vencimento"], errors="coerce").dt.normalize()
    venc = venc.where(known)
    signed = pd.to_datetime(stamped.get("signed_at"), errors="coerce")
    sig_day = signed.dt.normalize()
    day = pd.Timestamp(dia).normalize()
    stamped["lm_vencimento"] = venc
    stamped["diferenca"] = (venc - sig_day).dt.days.astype("Int64")
    stamped["situacao"] = SIT_SEM_PRAZO
    stamped.loc[known & signed.isna(), "situacao"] = SIT_ABERTO
    stamped.loc[known & signed.notna() & sig_day.le(venc), "situacao"] = SIT_DENTRO
    stamped.loc[known & signed.notna() & sig_day.gt(venc), "situacao"] = SIT_FORA
    stamped["vence_no_dia"] = known & venc.eq(day)
    state = stamped.get("dest_state", pd.Series("", index=stamped.index)).astype("string").str.strip()
    city = stamped.get("dest_city", pd.Series("", index=stamped.index)).astype("string").str.strip()
    stamped["concateno"] = (state + city).str.replace(" ", "", regex=False)
    return stamped.reset_index(drop=True)


def build_performance_sla(
    entregues: pd.DataFrame | None,
    abertos: pd.DataFrame | None,
    prazo_lookup: pd.DataFrame,
    responsabilidade: pd.DataFrame,
    dia: pd.Timestamp,
    *,
    region: str | None = "Regional Sul",
) -> pd.DataFrame:
    """Pacotes cujo vencimento last-mile cai em ``dia``."""
    classified = classify_sla(
        entregues,
        abertos,
        prazo_lookup,
        responsabilidade,
        dia,
        region=region,
    )
    if classified.empty or "vence_no_dia" not in classified.columns:
        return classified
    return classified.loc[classified["vence_no_dia"]].reset_index(drop=True)


def performance_pivot(detail: pd.DataFrame, *, group: str = "base") -> pd.DataFrame:
    """DIN: volumetria e percentual de cada situação no grupo."""
    columns = [
        "Grupo",
        "Dentro",
        "Dentro %",
        "Aberto",
        "Aberto %",
        "Fora",
        "Fora %",
        "Total",
        "Total %",
    ]
    if detail.empty or group not in detail.columns:
        return pd.DataFrame(columns=columns)
    work = detail.copy()
    work["_grupo"] = work[group].astype("string").str.strip()
    work["_grupo"] = work["_grupo"].mask(work["_grupo"].isna() | work["_grupo"].isin(["", "<NA>", "nan"]), "SEM")
    rows = []
    for name, part in work.groupby("_grupo", dropna=False):
        total = int(len(part))
        dentro = int(part["situacao"].eq(SIT_DENTRO).sum())
        aberto = int(part["situacao"].eq(SIT_ABERTO).sum())
        fora = int(part["situacao"].eq(SIT_FORA).sum())
        rows.append(
            {
                "Grupo": name,
                "Dentro": dentro,
                "Dentro %": dentro / total if total else 0,
                "Aberto": aberto,
                "Aberto %": aberto / total if total else 0,
                "Fora": fora,
                "Fora %": fora / total if total else 0,
                "Total": total,
                "Total %": 1 if total else 0,
            }
        )
    out = pd.DataFrame(rows, columns=columns)
    return out.sort_values(["Dentro", "Total", "Grupo"], ascending=[False, False, True]).reset_index(drop=True)


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", str(name).strip(), flags=re.UNICODE)
    return cleaned.strip("_")[:80] or "SEM"


def _write_din(writer: pd.ExcelWriter, pivot: pd.DataFrame, *, title: str, sheet: str) -> None:
    sheet = sheet[:31]
    workbook = writer.book
    ws = workbook.add_worksheet(sheet)
    writer.sheets[sheet] = ws
    title_fmt = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#14532d"})
    head_fmt = workbook.add_format(
        {"bold": True, "bg_color": "#14532d", "font_color": "#ffffff", "align": "center", "text_wrap": True}
    )
    pct_fmt = workbook.add_format({"num_format": "0.0%", "align": "center"})
    int_fmt = workbook.add_format({"num_format": "#,##0", "align": "center"})
    base_fmt = workbook.add_format({"bold": True})
    total_fmt = workbook.add_format({"bold": True, "bg_color": "#dcfce7", "num_format": "#,##0"})
    total_pct = workbook.add_format({"bold": True, "bg_color": "#dcfce7", "num_format": "0.0%"})
    ws.write(1, 1, title, title_fmt)
    ws.write(2, 1, "D0 com inbound depois das 08:00 vence no dia seguinte. Diferença positiva = entregue antes do vencimento.")
    headers = [
        "BASE",
        "ENTREGUE DENTRO DO PRAZO",
        "%",
        "EM ABERTO",
        "%",
        "ENTREGUE FORA DO PRAZO",
        "%",
        "Total",
        "Total %",
    ]
    for col, label in enumerate(headers):
        ws.write(4, col, label, head_fmt)
    for row_i, row in enumerate(pivot.itertuples(index=False), start=5):
        ws.write(row_i, 0, row[0], base_fmt)
        ws.write_number(row_i, 1, int(row[1]), int_fmt)
        ws.write_number(row_i, 2, float(row[2]), pct_fmt)
        ws.write_number(row_i, 3, int(row[3]), int_fmt)
        ws.write_number(row_i, 4, float(row[4]), pct_fmt)
        ws.write_number(row_i, 5, int(row[5]), int_fmt)
        ws.write_number(row_i, 6, float(row[6]), pct_fmt)
        ws.write_number(row_i, 7, int(row[7]), int_fmt)
        ws.write_number(row_i, 8, float(row[8]), pct_fmt)
    total_row = 5 + len(pivot)
    if not pivot.empty:
        total = int(pivot["Total"].sum())
        dentro = int(pivot["Dentro"].sum())
        aberto = int(pivot["Aberto"].sum())
        fora = int(pivot["Fora"].sum())
        ws.write(total_row, 0, "Total Geral", total_fmt)
        for col, value in ((1, dentro), (3, aberto), (5, fora), (7, total)):
            ws.write_number(total_row, col, value, total_fmt)
        for col, value in (
            (2, dentro / total if total else 0),
            (4, aberto / total if total else 0),
            (6, fora / total if total else 0),
            (8, 1 if total else 0),
        ):
            ws.write_number(total_row, col, value, total_pct)
    ws.set_column(0, 0, 22)
    ws.set_column(1, 8, 16)
    ws.freeze_panes(5, 1)


def _detail_frame(detail: pd.DataFrame) -> pd.DataFrame:
    rename = {
        "waybill": "Waybill",
        "status": "Status",
        "responsavel": "Responsáveis",
        "tipo_operacao": "Operação",
        "base": "Base",
        "pre_point": "Pré-entrega",
        "dest_city": "Cidade",
        "dest_state": "UF",
        "courier": "Entregador",
        "base_inbound": "Inbound",
        "signed_at": "Assinatura",
        "concateno": "CONCATENO",
        "lm_dias": "PRAZO",
        "lm_vencimento": "VENCIMENTO",
        "diferenca": "DIFERENÇA",
        "situacao": "SITUAÇÃO",
        "vence_no_dia": "Vence no dia",
        "corte_8h": "Corte 08h",
    }
    keep = [col for col in rename if col in detail.columns]
    out = detail.loc[:, keep].rename(columns=rename)
    return out.sort_values(["SITUAÇÃO", "Base", "Cidade"], kind="stable")


def write_performance_book(
    destination: str | Path,
    detail: pd.DataFrame,
    *,
    title: str,
    include_detail: bool = True,
    audit: pd.DataFrame | None = None,
    carteira: pd.DataFrame | None = None,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    by_base = performance_pivot(detail, group="base")
    by_tipo = performance_pivot(detail, group="tipo_operacao")
    with pd.ExcelWriter(destination, engine="xlsxwriter") as writer:
        _write_din(writer, by_base, title=title, sheet="DIN PERFORMANCE LM")
        _write_din(writer, by_tipo, title=title + " · operação", sheet="POR OPERAÇÃO")
        if carteira is not None and not carteira.empty:
            _write_din(writer, carteira, title=title + " · extração inteira", sheet="CARTEIRA")
        sheet = audit if audit is not None else detail
        if include_detail and not sheet.empty:
            _detail_frame(sheet).to_excel(writer, sheet_name="AUDITORIA", index=False)
    return destination


def write_owner_books(
    detail: pd.DataFrame,
    folder: str | Path,
    *,
    dia: pd.Timestamp,
    audit: pd.DataFrame | None = None,
) -> list[Path]:
    """Um workbook por responsável: DIN do dia e a extração inteira da carteira."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp(dia).strftime("%d.%m")
    written: list[Path] = []
    source = audit if audit is not None else detail
    if source.empty or "responsavel" not in source.columns:
        return written
    owners = sorted(source["responsavel"].dropna().astype(str).unique())
    for owner in owners:
        full = source.loc[source["responsavel"].astype(str).eq(owner)].copy()
        if full.empty:
            continue
        day = full.loc[full["vence_no_dia"]] if "vence_no_dia" in full.columns else full
        title = f"PERFORMANCE SLA LM {stamp} · {owner}"
        path = folder / _slug(owner) / f"PERFORMANCE_SLA_LM_{pd.Timestamp(dia).date().isoformat()}.xlsx"
        write_performance_book(path, day, title=title, include_detail=True, audit=full)
        written.append(path)
    return written
