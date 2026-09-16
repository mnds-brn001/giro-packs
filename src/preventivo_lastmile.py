"""Preventivo Last Mile — RESUMO da matriz + copy de vencendo hoje.

Replica o PROCV da aba BD contra o cálculo do analista (coluna Resumo Prazo)
e monta a cobrança visual da dinâmica: entregador na rota, cidade no piso.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .backlog_lastmile import (
    LAST_MILE_KEYS,
    _append_grouped_lines,
    _append_waybill_copy_block,
    _clean_base,
    _fmt_cell,
    _is_route_status,
)
from .columns import COL

RESUMO_VENCE_HOJE = "VENCE HOJE / 赢在今天"
RESUMO_FORA = "FORA DO PRAZO / 截止日期后"
RESUMO_DEPOIS = "VENCE DEPOIS / 稍後獲勝"

PREVENTIVO_KEYS = LAST_MILE_KEYS + ("last_track", "dest_state")


@dataclass(frozen=True)
class PreventivoRoutine:
    as_of: pd.Timestamp
    detail: pd.DataFrame
    pivot: pd.DataFrame
    vence_hoje: pd.DataFrame
    messages: dict[str, str]


def _read_excel(path: Path, *, sheet_name=0, usecols=None) -> pd.DataFrame:
    kwargs: dict = {"sheet_name": sheet_name}
    if usecols is not None:
        kwargs["usecols"] = usecols
    try:
        return pd.read_excel(path, engine="calamine", **kwargs)
    except (ValueError, ImportError, Exception):
        return pd.read_excel(path, **kwargs)


def _pick_sheet(path: Path, preferred: tuple[str, ...]) -> str:
    xl = pd.ExcelFile(path, engine="calamine")
    names = xl.sheet_names
    for name in preferred:
        if name in names:
            return name
    return names[0]


def _find_resumo_column(columns: list) -> str | None:
    for col in columns:
        label = str(col).strip().upper()
        if label in {"RESUMO", "RESUMO PRAZO"} or label.startswith("RESUMO"):
            return col
    return None


def load_hq_resumo(source: str | Path) -> pd.DataFrame:
    """Lê waybill + Resumo Prazo (coluna 95 / Detalhes1 ou BD)."""
    path = Path(source)
    sheet = _pick_sheet(path, ("Detalhes1", "BD", "bd"))
    raw = _read_excel(path, sheet_name=sheet)
    waybill_col = raw.columns[0]
    resumo_col = _find_resumo_column(list(raw.columns))
    if resumo_col is None:
        if raw.shape[1] < 95:
            raise ValueError(f"{path.name}: não achei a coluna RESUMO/Resumo Prazo.")
        resumo_col = raw.columns[94]
    out = pd.DataFrame(
        {
            "waybill": raw[waybill_col].astype("string").str.strip(),
            "resumo": raw[resumo_col].astype("string").str.strip(),
        }
    )
    out = out.loc[out["waybill"].notna() & ~out["waybill"].isin(["", "nan", "None"])]
    return out.drop_duplicates("waybill", keep="last")


def load_preventivo_bd(source: str | Path) -> pd.DataFrame:
    """Aba BD atualizada do Preventivo Lastmile (status, ponto, entregador, cidade)."""
    path = Path(source)
    sheet = _pick_sheet(path, ("BD", "bd", "Detalhes1"))
    raw = _read_excel(path, sheet_name=sheet)
    resumo_col = _find_resumo_column(list(raw.columns))
    reverse = {label: key for key, label in COL.items() if key in PREVENTIVO_KEYS}
    work = raw.rename(columns=reverse)
    if resumo_col is not None and resumo_col in raw.columns:
        work["resumo"] = raw[resumo_col].astype("string").str.strip()
    if "waybill" not in work.columns:
        work["waybill"] = raw.iloc[:, 0].astype("string").str.strip()
    else:
        work["waybill"] = work["waybill"].astype("string").str.strip()
    return work


def load_prazo_tables(source: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lê prazo D0/D+N por cidade e a carteira de responsabilidade por base."""
    path = Path(source)
    xl = pd.ExcelFile(path, engine="calamine")
    names = xl.sheet_names
    prazo_sheet = next((n for n in names if "Last Mile" in n or "时效" in n), names[min(2, len(names) - 1)])
    resp_sheet = next((n for n in names if "RESPONSABILIDADE" in n.upper() or "责任" in n), names[1] if len(names) > 1 else names[0])

    raw_prazo = _read_excel(path, sheet_name=prazo_sheet)
    raw_prazo.columns = [str(c).strip() for c in raw_prazo.columns]
    state_col = next(c for c in raw_prazo.columns if c.lower().startswith("state"))
    city_col = next(c for c in raw_prazo.columns if "city" in c.lower())
    base_col = next((c for c in raw_prazo.columns if "base" in c.lower() or "code" in c.lower()), raw_prazo.columns[3])
    dias_col = next(c for c in raw_prazo.columns if "dias" in c.lower() or c.upper() == "TIME DELIVERY DIAS")
    lookup = pd.DataFrame(
        {
            "dest_state": raw_prazo[state_col].astype("string").str.strip().str.upper(),
            "dest_city_n": raw_prazo[city_col].astype("string").str.strip().str.casefold(),
            "base_n": raw_prazo[base_col].astype("string").str.strip(),
            "lm_dias": pd.to_numeric(raw_prazo[dias_col], errors="coerce"),
        }
    ).dropna(subset=["dest_city_n", "lm_dias"])

    raw_resp = _read_excel(path, sheet_name=resp_sheet)
    raw_resp.columns = [str(c).strip() for c in raw_resp.columns]
    resp = pd.DataFrame(
        {
            "base": raw_resp.iloc[:, 0].astype("string").str.strip(),
            "responsavel": raw_resp.iloc[:, 1].astype("string").str.strip(),
        }
    ).dropna(subset=["base"])
    return lookup, resp


def apply_inbound_resumo(bd: pd.DataFrame, prazo_lookup: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """RESUMO operacional: data de inbound + TIME DELIVERY DIAS (D0/D+1/D+2)."""
    work = bd.copy()
    if "base_inbound" not in work.columns:
        raise ValueError("A extração precisa da coluna de inbound no ponto para calcular o RESUMO.")
    inbound = pd.to_datetime(work["base_inbound"], errors="coerce").dt.normalize()
    city_n = work.get("dest_city", pd.Series("", index=work.index)).astype("string").str.strip().str.casefold()
    state_n = work.get("dest_state", pd.Series("", index=work.index)).astype("string").str.strip().str.upper()
    base_n = work.get("last_point", work.get("entry_point", pd.Series("", index=work.index)))
    base_n = base_n.astype("string").str.strip()

    keys = pd.DataFrame(
        {"dest_state": state_n.to_numpy(), "dest_city_n": city_n.to_numpy(), "base_n": base_n.to_numpy()}
    )
    by_base = prazo_lookup.drop_duplicates(["dest_state", "dest_city_n", "base_n"], keep="first")
    dias = keys.merge(by_base, on=["dest_state", "dest_city_n", "base_n"], how="left")["lm_dias"]
    miss = dias.isna()
    if miss.any():
        by_city = prazo_lookup.drop_duplicates(["dest_state", "dest_city_n"], keep="first")
        fb = keys.merge(by_city, on=["dest_state", "dest_city_n"], how="left")["lm_dias"]
        dias = dias.fillna(fb)
    dias = pd.Series(dias.to_numpy(), index=work.index)

    venc = inbound + pd.to_timedelta(dias.fillna(0), unit="D")
    reference = pd.Timestamp(as_of).normalize()
    resumo = pd.Series(RESUMO_DEPOIS, index=work.index, dtype="string")
    resumo = resumo.mask(venc < reference, RESUMO_FORA)
    resumo = resumo.mask(venc.eq(reference), RESUMO_VENCE_HOJE)
    resumo = resumo.mask(inbound.isna() | dias.isna(), pd.NA)
    work["lm_dias"] = dias
    work["lm_vencimento"] = venc
    work["resumo"] = resumo
    return work


def bases_for_owner(responsabilidade: pd.DataFrame, owner: str) -> list[str]:
    mask = responsabilidade["responsavel"].str.contains(owner.strip(), case=False, na=False)
    return sorted(responsabilidade.loc[mask, "base"].dropna().unique())


def apply_hq_resumo(bd: pd.DataFrame, hq_resumo: pd.DataFrame) -> pd.DataFrame:
    """PROCV waybill → Resumo Prazo da matriz. Sobrescreve RESUMO local se houver HQ."""
    work = bd.copy()
    lookup = hq_resumo.rename(columns={"resumo": "_resumo_hq"})
    work = work.merge(lookup, on="waybill", how="left")
    if "_resumo_hq" in work.columns:
        work["resumo"] = work["_resumo_hq"].fillna(work.get("resumo"))
        work = work.drop(columns=["_resumo_hq"])
    return work


def _normalize_resumo(series: pd.Series) -> pd.Series:
    text = series.astype("string")
    out = pd.Series(pd.NA, index=series.index, dtype="string")
    out = out.mask(text.str.contains("VENCE HOJE", case=False, na=False), RESUMO_VENCE_HOJE)
    out = out.mask(text.str.contains("FORA DO PRAZO", case=False, na=False), RESUMO_FORA)
    out = out.mask(text.str.contains("VENCE DEPOIS", case=False, na=False), RESUMO_DEPOIS)
    return out


def _pivot_resumo(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=["Base", "Status", RESUMO_FORA, RESUMO_VENCE_HOJE, RESUMO_DEPOIS, "Total Geral"]
        )
    work = detail.copy()
    work["_status"] = work.get("status", "SEM STATUS").astype("string").fillna("SEM STATUS")
    counts = (
        work.groupby(["_base", "_status", "resumo"], dropna=False)
        .size()
        .reset_index(name="qtd")
    )
    pivot = counts.pivot_table(
        index=["_base", "_status"],
        columns="resumo",
        values="qtd",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    for col in (RESUMO_FORA, RESUMO_VENCE_HOJE, RESUMO_DEPOIS):
        if col not in pivot.columns:
            pivot[col] = 0
    pivot = pivot.rename(columns={"_base": "Base", "_status": "Status"})
    pivot["Total Geral"] = pivot[[RESUMO_FORA, RESUMO_VENCE_HOJE, RESUMO_DEPOIS]].sum(axis=1)
    return pivot.sort_values(
        ["Total Geral", "Base", "Status"], ascending=[False, True, True]
    ).reset_index(drop=True)


def preventivo_message_for_base(base: str, vence_hoje: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """Copy de cobrança do vencendo hoje: rota=entregador, piso=cidade."""
    date_label = as_of.strftime("%d/%m/%Y")
    status = vence_hoje["status"] if "status" in vence_hoje.columns else pd.Series("", index=vence_hoje.index)
    route = vence_hoje.loc[status.map(_is_route_status)]
    floor = vence_hoje.loc[~status.map(_is_route_status)]

    lines = [
        "🚨 *PRIORIDADE MÁXIMA – ENCERRAMENTO DE TURNO E SLA HOJE*",
        "",
        f"Base: *{base}* | Referência: *{date_label}*",
        f"VENCE HOJE: *{len(vence_hoje)}*  (em rota: {len(route)} · no piso: {len(floor)})",
        "",
        "Pessoal, boa tarde.",
        "",
        "Mapeamos os pedidos com vencimento limite para HOJE. Precisamos da garantia de baixa ou conclusão de entrega ainda no turno de hoje para evitar estouro de SLA na base.",
    ]

    if route.empty and floor.empty:
        lines.extend(
            [
                "",
                "Não há pacote vencendo hoje nesta extração.",
            ]
        )
        return "\n".join(lines)

    if not route.empty:
        lines.extend(["", "⚠️ *EM ROTA — prioridade total nos AJs abaixo:*"])
        _append_grouped_lines(
            lines,
            route,
            group_key="courier",
            empty_group="SEM MOTORISTA",
            line_fn=lambda row: (
                f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
                f"{_fmt_cell(row.get('last_track'), _fmt_cell(row.get('status'), 'Em rota de entrega'))}\t"
                f"{_fmt_cell(row.get('courier'), 'SEM MOTORISTA')}"
            ),
        )

    if not floor.empty:
        lines.extend(["", "📦 *NO PISO — vencendo HOJE, atualizar movimentação:*"])
        _append_grouped_lines(
            lines,
            floor,
            group_key="dest_city",
            empty_group="SEM CIDADE",
            line_fn=lambda row: (
                f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
                f"{_fmt_cell(row.get('status'), 'No piso')}\t"
                f"{_fmt_cell(row.get('dest_city'), 'SEM CIDADE')}"
            ),
        )

    _append_waybill_copy_block(lines, vence_hoje, title="AJs vence hoje para copiar")
    lines.extend(
        [
            "",
            "📌 Ação necessária: alinhamento imediato com os motoristas para garantia de baixa no app até o fechamento. Contamos com o empenho da base para fechar o dia sem pendências! 🚚📦",
        ]
    )
    return "\n".join(lines)


def build_preventivo_routine(
    bd: pd.DataFrame,
    *,
    as_of: pd.Timestamp | str | date | None = None,
    hq_resumo: pd.DataFrame | None = None,
    prazo_lookup: pd.DataFrame | None = None,
    only_bases: list[str] | None = None,
) -> PreventivoRoutine:
    """Junta a extração + RESUMO (HQ ou inbound+D0/D+N) e gera a fila de VENCE HOJE."""
    reference = pd.Timestamp(as_of or pd.Timestamp.now()).normalize()
    work = bd.copy()
    if prazo_lookup is not None:
        work = apply_inbound_resumo(work, prazo_lookup, reference)
    if hq_resumo is not None:
        work = apply_hq_resumo(work, hq_resumo)
    if "resumo" not in work.columns:
        raise ValueError(
            "Sem RESUMO. Passe --prazo (inbound + D0/D+N) ou --hq (PREVENTIVO LM da matriz)."
        )

    work["resumo"] = _normalize_resumo(work["resumo"])
    base_series = work["last_point"] if "last_point" in work.columns else work.get("entry_point")
    if base_series is None:
        raise ValueError("BD sem último ponto de rastreio nem ponto de entrada.")
    work["_base"] = _clean_base(base_series)
    if only_bases:
        allowed = {str(b).strip() for b in only_bases}
        work = work.loc[work["_base"].isin(allowed)].copy()

    detail_cols = [
        c
        for c in (
            "_base",
            "waybill",
            "status",
            "resumo",
            "courier",
            "dest_city",
            "last_track",
            "last_point",
            "entry_point",
            "due_at",
            "lm_dias",
            "lm_vencimento",
        )
        if c in work.columns
    ]
    detail = work[detail_cols].rename(columns={"_base": "base"})
    vence = work.loc[work["resumo"].eq(RESUMO_VENCE_HOJE)].copy()
    messages = {
        base: preventivo_message_for_base(base, group, reference)
        for base, group in vence.groupby("_base", dropna=False, sort=True)
    }
    return PreventivoRoutine(
        as_of=reference,
        detail=detail,
        pivot=_pivot_resumo(work),
        vence_hoje=vence.rename(columns={"_base": "base"}),
        messages=messages,
    )
