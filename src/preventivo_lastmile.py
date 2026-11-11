"""Preventivo Last Mile — RESUMO da matriz + cobrança Fora do Prazo LM.

Replica o PROCV da aba BD contra o cálculo do analista (coluna Resumo Prazo)
e monta a cobrança da faixa crítica: Fora do Prazo LM com +9 dias.
A dinâmica da relação continua entregador na rota e cidade no piso.
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
FORA_MIN_DAYS = 9

PREVENTIVO_KEYS = LAST_MILE_KEYS + ("last_track", "dest_state")


@dataclass(frozen=True)
class PreventivoRoutine:
    as_of: pd.Timestamp
    detail: pd.DataFrame
    pivot: pd.DataFrame
    vence_hoje: pd.DataFrame
    fora_lm: pd.DataFrame
    messages: dict[str, str]


def _read_excel(path: Path, *, sheet_name=0, usecols=None, header=0) -> pd.DataFrame:
    kwargs: dict = {"sheet_name": sheet_name, "header": header}
    if usecols is not None:
        kwargs["usecols"] = usecols
    try:
        return pd.read_excel(path, engine="calamine", **kwargs)
    except (ValueError, ImportError, Exception):
        return pd.read_excel(path, **kwargs)


def _excel_sheets(path: Path) -> list[str]:
    try:
        return list(pd.ExcelFile(path, engine="calamine").sheet_names)
    except (ValueError, ImportError, Exception):
        return list(pd.ExcelFile(path).sheet_names)


def _pick_named_sheet(names: list[str], pred) -> str | None:
    return next((name for name in names if pred(name)), None)


def _find_header(columns, *needles: str) -> str | None:
    for col in columns:
        folded = str(col).casefold()
        if any(needle.casefold() in folded for needle in needles):
            return col
    return None


def _as_text(series: pd.Series) -> pd.Series:
    def cell(value: object) -> object:
        if pd.isna(value):
            return pd.NA
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        text = str(value).strip()
        return pd.NA if text in {"", "nan", "None", "<NA>"} else text

    return series.map(cell).astype("string")


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


@dataclass(frozen=True)
class PrazoBook:
    """Prazo por cidade, carteira por base, etapa do rastreio e assistentes."""

    lookup: pd.DataFrame
    responsabilidade: pd.DataFrame
    etapa: pd.DataFrame
    assistentes: pd.DataFrame


_EMPTY_ETAPA = ("ultimo_rastreio", "etapa")
_EMPTY_ASSISTENTES = ("assistente", "demanda", "demanda_extra")


def _is_carteira_sheet(name: str) -> bool:
    upper = name.upper()
    if "ETAPA" in upper:
        return False
    return "RESPONSABILIDADE" in upper or "责任" in name


def _load_lookup(path: Path, sheet: str) -> pd.DataFrame:
    raw = _read_excel(path, sheet_name=sheet)
    raw.columns = [str(c).strip() for c in raw.columns]
    state_col = next(c for c in raw.columns if c.lower().startswith("state"))
    city_col = next(c for c in raw.columns if "city" in c.lower())
    base_col = next((c for c in raw.columns if "base" in c.lower() or "code" in c.lower()), raw.columns[3])
    dias_col = next(c for c in raw.columns if "dias" in c.lower() or c.upper() == "TIME DELIVERY DIAS")
    contato_col = _find_header(raw.columns, "responsible", "contato")
    fone_col = _find_header(raw.columns, "phone", "telefone", "fone", "电话")
    lookup = pd.DataFrame(
        {
            "dest_state": raw[state_col].astype("string").str.strip().str.upper(),
            "dest_city_n": raw[city_col].astype("string").str.strip().str.casefold(),
            "base_n": raw[base_col].astype("string").str.strip(),
            "lm_dias": pd.to_numeric(raw[dias_col], errors="coerce"),
            "contato": _as_text(raw[contato_col]) if contato_col else pd.NA,
            "telefone": _as_text(raw[fone_col]) if fone_col else pd.NA,
        }
    )
    return lookup.dropna(subset=["dest_city_n", "lm_dias"])


def _load_carteira(path: Path, sheet: str) -> pd.DataFrame:
    raw = _read_excel(path, sheet_name=sheet)
    raw.columns = [str(c).strip() for c in raw.columns]
    cols = list(raw.columns)
    owner_col = next((c for c in cols if "responsab" in c.lower() or "责任" in c), None)
    base_col = next(
        (
            c
            for c in cols
            if c != owner_col
            and any(tok in c.lower() for tok in ("pré-entrega", "pre-entrega", "ponto", "base", "网点"))
        ),
        cols[0],
    )
    if owner_col is None:
        owner_col = cols[1] if len(cols) > 1 else cols[0]
    payload = {
        "base": raw[base_col].astype("string").str.strip(),
        "responsavel": raw[owner_col].astype("string").str.strip(),
    }
    tipo_col = next(
        (c for c in cols if c not in {owner_col, base_col} and ("opera" in c.lower() or "操作" in c)),
        None,
    )
    uf_col = next((c for c in cols if str(c).strip().upper() == "UF"), None)
    sac_col = next((c for c in cols if str(c).strip().upper() == "SAC"), None)
    if tipo_col is not None:
        payload["tipo_operacao"] = raw[tipo_col].astype("string").str.strip()
    if uf_col is not None:
        payload["uf"] = raw[uf_col].astype("string").str.strip()
    if sac_col is not None:
        payload["sac"] = raw[sac_col].astype("string").str.strip()
    resp = pd.DataFrame(payload).dropna(subset=["base"])
    return resp.loc[~resp["base"].isin(["", "nan", "None", "<NA>"])].reset_index(drop=True)


def _load_etapa(path: Path, sheet: str | None) -> pd.DataFrame:
    if sheet is None:
        return pd.DataFrame(columns=list(_EMPTY_ETAPA))
    raw = _read_excel(path, sheet_name=sheet)
    raw.columns = [str(c).strip() for c in raw.columns]
    track_col = _find_header(raw.columns, "rastreio", "ultimo", "último") or raw.columns[0]
    etapa_col = _find_header(raw.columns, "etapa") or raw.columns[min(1, len(raw.columns) - 1)]
    etapa = pd.DataFrame(
        {
            "ultimo_rastreio": _as_text(raw[track_col]),
            "etapa": _as_text(raw[etapa_col]),
        }
    ).dropna(subset=["ultimo_rastreio"])
    return etapa.reset_index(drop=True)


def _load_assistentes(path: Path, sheet: str | None) -> pd.DataFrame:
    if sheet is None:
        return pd.DataFrame(columns=list(_EMPTY_ASSISTENTES))
    raw = _read_excel(path, sheet_name=sheet, header=None)
    header_at = None
    for idx, row in raw.iterrows():
        blob = " ".join(str(v) for v in row.tolist() if pd.notna(v)).casefold()
        if "demanda" in blob:
            header_at = idx
            break
    if header_at is None:
        return pd.DataFrame(columns=list(_EMPTY_ASSISTENTES))
    headers = [str(v).strip() if pd.notna(v) else f"col_{i}" for i, v in enumerate(raw.loc[header_at].tolist())]
    body = raw.iloc[int(header_at) + 1 :].copy()
    body.columns = headers
    name_col = _find_header(body.columns, "responsab", "责任") or body.columns[0]
    extra_col = _find_header(body.columns, "extra", "额外")
    demanda_col = next(
        (c for c in body.columns if c != extra_col and "demanda" in str(c).casefold()),
        None,
    )
    frame = pd.DataFrame(
        {
            "assistente": _as_text(body[name_col]),
            "demanda": _as_text(body[demanda_col]) if demanda_col else pd.NA,
            "demanda_extra": _as_text(body[extra_col]) if extra_col else pd.NA,
        }
    ).dropna(subset=["assistente"])
    return frame.reset_index(drop=True)


def load_prazo_book(source: str | Path) -> PrazoBook:
    """Lê a responsabilidade nova: prazo, carteira, etapa do rastreio e assistentes.

    A carteira é a folha com RESPONSABILIDADE no nome. `RESP. ETAPA` fica de fora
    dessa escolha. UF e SAC continuam opcionais para a planilha antiga.
    """
    path = Path(source)
    names = _excel_sheets(path)
    prazo_sheet = _pick_named_sheet(
        names,
        lambda name: ("last mile" in name.lower() or "时效" in name) and "etapa" not in name.lower(),
    ) or names[min(2, len(names) - 1)]
    resp_sheet = _pick_named_sheet(names, _is_carteira_sheet) or (names[1] if len(names) > 1 else names[0])
    etapa_sheet = _pick_named_sheet(names, lambda name: "etapa" in name.lower())
    demanda_sheet = _pick_named_sheet(names, lambda name: "demad" in name.lower())
    return PrazoBook(
        lookup=_load_lookup(path, prazo_sheet),
        responsabilidade=_load_carteira(path, resp_sheet),
        etapa=_load_etapa(path, etapa_sheet),
        assistentes=_load_assistentes(path, demanda_sheet),
    )


def load_prazo_tables(source: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lê prazo D0/D+N por cidade e a carteira de responsabilidade por base."""
    book = load_prazo_book(source)
    return book.lookup, book.responsabilidade


def apply_inbound_resumo(bd: pd.DataFrame, prazo_lookup: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """RESUMO operacional: data de inbound + TIME DELIVERY DIAS (D0/D+1/D+2)."""
    work = bd.copy()
    if "base_inbound" not in work.columns:
        raise ValueError("A extração precisa da coluna de inbound no ponto para calcular o RESUMO.")
    inbound_ts = pd.to_datetime(work["base_inbound"], errors="coerce")
    inbound = inbound_ts.dt.normalize()
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
    # D0 só vale no mesmo dia se o inbound foi até 08:00. Depois disso o prazo é D+1.
    after_cut = (inbound_ts - inbound).gt(pd.Timedelta(hours=8)).fillna(False)
    corte = dias.eq(0) & after_cut
    efetivo = dias.where(~corte, 1)

    venc = inbound + pd.to_timedelta(efetivo.fillna(0), unit="D")
    reference = pd.Timestamp(as_of).normalize()
    resumo = pd.Series(RESUMO_DEPOIS, index=work.index, dtype="string")
    resumo = resumo.mask(venc < reference, RESUMO_FORA)
    resumo = resumo.mask(venc.eq(reference), RESUMO_VENCE_HOJE)
    resumo = resumo.mask(inbound.isna() | dias.isna(), pd.NA)
    work["lm_dias"] = dias
    work["corte_8h"] = pd.Series(pd.NA, index=work.index, dtype="string")
    work.loc[corte, "corte_8h"] = "D+1"
    work["lm_vencimento"] = venc
    work["dias_fora"] = (reference - venc).dt.days
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


def _dias_fora_series(work: pd.DataFrame, reference: pd.Timestamp) -> pd.Series:
    """Dias além do vencimento de last mile. Positivo = fora do prazo."""
    if "lm_vencimento" in work.columns:
        venc = pd.to_datetime(work["lm_vencimento"], errors="coerce").dt.normalize()
        computed = (pd.Timestamp(reference).normalize() - venc).dt.days
        if "dias_fora" in work.columns:
            return computed.fillna(pd.to_numeric(work["dias_fora"], errors="coerce"))
        return computed
    if "dias_fora" in work.columns:
        return pd.to_numeric(work["dias_fora"], errors="coerce")
    return pd.Series(pd.NA, index=work.index, dtype="Float64")


def _dias_label(val) -> str:
    days = pd.to_numeric(val, errors="coerce")
    if pd.isna(days):
        return "s/dias"
    return f"{int(days)}d"


def preventivo_message_for_base(base: str, fora_lm: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """Copy de cobrança: só Fora do Prazo LM com +9 dias. Rota=entregador, piso=cidade."""
    date_label = as_of.strftime("%d/%m/%Y")
    status = fora_lm["status"] if "status" in fora_lm.columns else pd.Series("", index=fora_lm.index)
    route = fora_lm.loc[status.map(_is_route_status)]
    floor = fora_lm.loc[~status.map(_is_route_status)]

    lines = [
        "🚨 *FORA DO PRAZO LM — BAIXA E TRATATIVA*",
        "",
        f"Base: *{base}* | Referência: *{date_label}*",
        f"Fora do prazo LM +{FORA_MIN_DAYS}d: *{len(fora_lm)}*  (em rota: {len(route)} · no piso: {len(floor)})",
        "",
        "Pessoal, boa tarde.",
        "",
        "Analisando os pedidos *Fora do Prazo LM*, identifico os seguintes pedidos pendentes de baixa / tratativa. "
        f"O recorte é de *+{FORA_MIN_DAYS} dias* ou mais além do prazo de last mile.",
        "",
        "Conto com o apoio da base para eliminarmos estas pendências.",
        "",
        "Segue a relação:",
    ]

    if route.empty and floor.empty:
        lines.extend(["", "Não há pacote Fora do Prazo LM nesta faixa nesta extração."])
        return "\n".join(lines)

    if not route.empty:
        lines.extend(["", "🚚 *EM ROTA*"])
        _append_grouped_lines(
            lines,
            route,
            group_key="courier",
            empty_group="SEM MOTORISTA",
            line_fn=lambda row: (
                f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
                f"{_fmt_cell(row.get('last_track'), _fmt_cell(row.get('status'), 'Em rota de entrega'))}\t"
                f"{_fmt_cell(row.get('courier'), 'SEM MOTORISTA')}\t"
                f"{_dias_label(row.get('dias_fora'))}"
            ),
        )

    if not floor.empty:
        lines.extend(["", "📦 *NO PISO*"])
        _append_grouped_lines(
            lines,
            floor,
            group_key="dest_city",
            empty_group="SEM CIDADE",
            line_fn=lambda row: (
                f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
                f"{_fmt_cell(row.get('status'), 'No piso')}\t"
                f"{_fmt_cell(row.get('dest_city'), 'SEM CIDADE')}\t"
                f"{_dias_label(row.get('dias_fora'))}"
            ),
        )

    _append_waybill_copy_block(lines, fora_lm, title="AJs fora do prazo LM para copiar")
    lines.extend(
        [
            "",
            "📌 Ação necessária: localizar, atualizar a movimentação e finalizar (baixa no app ou tratativa válida). "
            "Contamos com o empenho da base para eliminar estas pendências.",
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
    """Junta a extração + RESUMO e gera a cobrança de Fora do Prazo LM +9 dias.

    ``vence_hoje`` continua no detalhe para conferência. A mensagem de WhatsApp
    leva só quem já estourou o prazo de last mile em ``FORA_MIN_DAYS`` ou mais.
    """
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
    work["dias_fora"] = _dias_fora_series(work, reference)
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
            "base_inbound",
            "issue_reason",
            "due_at",
            "lm_dias",
            "corte_8h",
            "lm_vencimento",
            "dias_fora",
        )
        if c in work.columns
    ]
    detail = work[detail_cols].rename(columns={"_base": "base"})
    vence = work.loc[work["resumo"].eq(RESUMO_VENCE_HOJE)].copy()
    fora = work.loc[
        work["resumo"].eq(RESUMO_FORA) & work["dias_fora"].ge(FORA_MIN_DAYS)
    ].copy()
    if not fora.empty:
        fora = fora.sort_values(["dias_fora", "waybill"], ascending=[False, True], na_position="last")
    messages = {
        base: preventivo_message_for_base(base, group, reference)
        for base, group in fora.groupby("_base", dropna=False, sort=True)
    }
    return PreventivoRoutine(
        as_of=reference,
        detail=detail,
        pivot=_pivot_resumo(work),
        vence_hoje=vence.rename(columns={"_base": "base"}),
        fora_lm=fora.rename(columns={"_base": "base"}),
        messages=messages,
    )
