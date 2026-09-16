"""Consolidação diária de backlog e exceções de SLA de Last Mile.

O módulo recebe a tabela mestra já normalizada por :mod:`src.loader` e produz
as filas operacionais para envio às bases. Não envia mensagens nem altera a
planilha de origem: a saída é revisável antes do envio no WhatsApp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .columns import COL

OPEN_STATUS_HINTS = ("rota", "armazenado", "recebido no ponto", "anomalia")
ROUTE_STATUS_HINT = "rota"
FLOOR_STATUS_HINT = "armazenado"

LAST_MILE_KEYS = (
    "waybill",
    "status",
    "due_at",
    "base_inbound",
    "region",
    "entry_point",
    "last_point",
    "delivery_point",
    "pre_point",
    "courier",
    "last_track_time",
    "last_track",
    "issue_reason",
    "delay_days",
    "dest_city",
    "dest_state",
)

DETAIL_COLUMNS = (
    "waybill",
    "status",
    "due_at",
    "entry_point",
    "last_point",
    "courier",
    "last_track_time",
    "last_track",
    "issue_reason",
    "delay_days",
    "dest_city",
)


@dataclass(frozen=True)
class LastMileRoutine:
    """Artefatos da rotina diária, prontos para exportar ou revisar."""

    as_of: pd.Timestamp
    regional_backlog: pd.DataFrame
    base_summary: pd.DataFrame
    due_today_route: pd.DataFrame
    due_today_floor: pd.DataFrame
    messages: dict[str, str]
    preventivo_messages: dict[str, str]


def _empty_detail() -> pd.DataFrame:
    return pd.DataFrame(columns=list(DETAIL_COLUMNS))


def load_lastmile_source(source: str | Path, *, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Leitura enxuta da Tabela Mestra — só colunas da rotina Last Mile.

    Evita o ``enrich()`` completo e tenta o motor ``calamine`` (bem mais
    rápido que openpyxl em XLSX grandes).
    """
    path = Path(source)
    excel_names = {COL[key] for key in LAST_MILE_KEYS if key in COL}
    usecols = lambda c: c in excel_names

    if path.suffix.lower() == ".csv":
        raw = pd.read_csv(path, usecols=usecols)
    elif path.suffix.lower() in {".parquet", ".pq"}:
        raw = pd.read_parquet(path)
        bilingual = [c for c in raw.columns if c in excel_names]
        if bilingual:
            raw = raw[bilingual]
        else:
            keep = [c for c in raw.columns if c in LAST_MILE_KEYS]
            if keep:
                raw = raw[keep]
    else:
        try:
            raw = pd.read_excel(path, engine="calamine", usecols=usecols)
        except (ValueError, ImportError, Exception):
            raw = pd.read_excel(path, usecols=usecols)

    reverse = {label: key for key, label in COL.items() if key in LAST_MILE_KEYS}
    work = raw.rename(columns=reverse)

    for key in ("due_at", "base_inbound", "last_track_time"):
        if key in work.columns:
            work[key] = pd.to_datetime(work[key], errors="coerce")
    if "delay_days" in work.columns:
        work["delay_days"] = pd.to_numeric(work["delay_days"], errors="coerce")

    status = work.get("status", pd.Series("", index=work.index)).astype("string")
    work["is_open"] = ~status.str.contains("entregue", case=False, na=False)
    work.attrs["as_of"] = pd.Timestamp(as_of or pd.Timestamp.now())
    return work


def _contains(series: pd.Series, text: str) -> pd.Series:
    return series.astype("string").str.contains(text, case=False, regex=False, na=False)


def _clean_base(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.strip()
    return cleaned.mask(cleaned.isin(["", "--", "nan", "None", "NaT"]), "SEM_BASE").fillna("SEM_BASE")


def _base_column(df: pd.DataFrame) -> str:
    """Prioriza o ponto físico atual, sem perder pacotes sem entrada registrada."""
    for column in ("entry_point", "last_point", "delivery_point", "pre_point"):
        if column in df.columns:
            return column
    raise ValueError("Nenhuma coluna de base disponível (entry_point/last_point/delivery_point/pre_point).")


def _is_open(df: pd.DataFrame) -> pd.Series:
    if "is_open" in df.columns:
        return df["is_open"].fillna(False)
    if "status" not in df.columns:
        return pd.Series(False, index=df.index)
    return ~_contains(df["status"], "entregue")


def _sort_queue(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    sort_columns = [column for column in ("_base", "courier", "due_at", "delay_days", "waybill") if column in df]
    ascending_by_column = {
        "_base": True,
        "courier": True,
        "due_at": True,
        "delay_days": False,
        "waybill": True,
    }
    ascending = [ascending_by_column[column] for column in sort_columns]
    return df.sort_values(sort_columns, ascending=ascending, na_position="last").reset_index(drop=True)


def _to_detail(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_detail()
    columns = ["_base", *[column for column in DETAIL_COLUMNS if column in df.columns]]
    detail = df[columns].copy().rename(columns={"_base": "base"})
    return detail


def _backlog_summary(backlog: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Base",
        "Mais de 15 dias de atraso",
        "Mais de 10 dias de atraso",
        "Mais de 5 dias de atraso",
        "Menos de 4 dias",
        "Total Geral",
    ]
    if backlog.empty:
        return pd.DataFrame(columns=columns)

    delay = pd.to_numeric(backlog.get("delay_days"), errors="coerce").fillna(0)
    grouped = pd.DataFrame(
        {
            "Base": backlog["_base"],
            "Mais de 15 dias de atraso": (delay > 15).astype(int),
            "Mais de 10 dias de atraso": ((delay > 10) & (delay <= 15)).astype(int),
            "Mais de 5 dias de atraso": ((delay > 5) & (delay <= 10)).astype(int),
            "Menos de 4 dias": (delay <= 5).astype(int),
        }
    ).groupby("Base", as_index=False).sum()
    grouped["Total Geral"] = grouped.iloc[:, 1:].sum(axis=1)
    return grouped.sort_values(["Total Geral", "Base"], ascending=[False, True]).reset_index(drop=True)


def _fmt_cell(val, empty: str = "—") -> str:
    if val is None or pd.isna(val):
        return empty
    text = str(val).strip()
    if text in {"", "--", "nan", "None", "NaT"}:
        return empty
    return text


def _delay_label(val) -> str:
    delay = pd.to_numeric(val, errors="coerce")
    if pd.isna(delay):
        return "s/atraso"
    days = int(round(float(delay)))
    return f"{days}d"


def _is_route_status(status) -> bool:
    return "rota" in str(status).lower()


def _append_grouped_lines(
    lines: list[str],
    frame: pd.DataFrame,
    *,
    group_key: str,
    empty_group: str,
    line_fn,
) -> None:
    if frame.empty:
        return
    work = frame.copy()
    if group_key not in work.columns:
        work[group_key] = empty_group
    grouped = work.groupby(group_key, dropna=False, sort=True)
    for key, group in grouped:
        label = _fmt_cell(key, empty_group)
        lines.append(f"\n*{label}*")
        for _, row in group.iterrows():
            lines.append(line_fn(row))


def _append_waybill_copy_block(
    lines: list[str],
    frame: pd.DataFrame,
    *,
    title: str = "AJs para copiar",
) -> None:
    """Lista só de waybills, uma por linha — cola no app em lote."""
    if frame.empty or "waybill" not in frame.columns:
        return
    unique: list[str] = []
    seen: set[str] = set()
    for raw in frame["waybill"].tolist():
        bill = _fmt_cell(raw, "")
        if not bill or bill in seen or bill == "SEM_WAYBILL":
            continue
        seen.add(bill)
        unique.append(bill)
    if not unique:
        return
    lines.extend(["", f"📋 *{title}* ({len(unique)})", ""])
    lines.extend(unique)


def _backlog_message_for_base(base: str, backlog: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """Copy de tratativa: foco na banda crítica de +10 dias (piso + rota)."""
    date_label = as_of.strftime("%d/%m/%Y")
    delay = pd.to_numeric(backlog.get("delay_days"), errors="coerce").fillna(0)
    n15 = int((delay > 15).sum())
    n10 = int(((delay > 10) & (delay <= 15)).sum())
    n5 = int(((delay > 5) & (delay <= 10)).sum())
    n4 = int((delay <= 5).sum())
    critical = backlog.loc[delay > 10].copy()
    if "delay_days" in critical.columns:
        critical = critical.sort_values("delay_days", ascending=False, na_position="last")

    lines = [
        "📋 *BACKLOG – TRATATIVA E FINALIZAÇÃO*",
        "",
        f"Base: *{base}* | Referência: *{date_label}*",
        f"Aberto: *{len(backlog)}*  |  Crítico +10d: *{len(critical)}*  (>15d: {n15} · 10–15d: {n10} · 5–10d: {n5} · ≤5d: {n4})",
        "",
        "Pessoal, boa tarde.",
        "",
        "Segue o backlog desta manhã. 📋 Precisamos de *tratativa e atualização* nos pacotes em atraso — prioridade máxima na banda crítica de *+10 dias*.",
        "",
        "⚠️ Pacotes parados no piso ou com motorista sem baixa impactam os indicadores da base.",
    ]

    if critical.empty:
        lines.extend(
            [
                "",
                "Não há pacote na banda crítica de +10 dias nesta extração.",
                "Manter giro do restante e atualizar movimentação dos pacotes parados.",
                "",
                "🤝 Conto com o apoio da base para fechar as pendências com prioridade.",
            ]
        )
        return "\n".join(lines)

    status = critical["status"] if "status" in critical.columns else pd.Series("", index=critical.index)
    route = critical.loc[status.map(_is_route_status)]
    floor = critical.loc[~status.map(_is_route_status)]

    if not floor.empty:
        lines.extend(["", "📦 *NO PISO — banda +10 dias* (cidade destinatário)"])
        _append_grouped_lines(
            lines,
            floor,
            group_key="dest_city",
            empty_group="SEM CIDADE",
            line_fn=lambda row: (
                f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
                f"{_fmt_cell(row.get('status'), 'No piso')}\t"
                f"{_delay_label(row.get('delay_days'))}"
            ),
        )

    if not route.empty:
        lines.extend(["", "🚚 *EM ROTA — banda +10 dias* (entregador + cidade)"])
        _append_grouped_lines(
            lines,
            route,
            group_key="courier",
            empty_group="SEM MOTORISTA",
            line_fn=lambda row: (
                f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
                f"{_fmt_cell(row.get('dest_city'), 'SEM CIDADE')}\t"
                f"{_delay_label(row.get('delay_days'))}"
            ),
        )

    _append_waybill_copy_block(lines, critical, title="AJs +10d para copiar")
    lines.extend(
        [
            "",
            "📌 Ação necessária: localizar, atualizar a movimentação e finalizar (baixa no app ou devolução válida). Contamos com o empenho da base! 🤝📦",
        ]
    )
    return "\n".join(lines)


def _preventivo_message_for_base(
    base: str, due_today: pd.DataFrame, backlog_count: int, as_of: pd.Timestamp
) -> str:
    """Copy de preventivo (vence hoje em rota) — segundo output, alinhamento posterior."""
    date_label = as_of.strftime("%d/%m/%Y")
    lines = [
        "🚨 *PRIORIDADE MÁXIMA – ENCERRAMENTO DE TURNO E SLA HOJE*",
        "",
        f"Base: *{base}* | Referência: *{date_label}*",
        f"Backlog aberto na base: *{backlog_count}* pacote(s).",
        "",
    ]
    if due_today.empty:
        lines.extend(
            [
                "Não há pacote em rota com vencimento hoje nesta extração.",
                "",
                "📌 Ação necessária: verificar pacotes parados na base, pendentes de tratativa, em devolução e com motoristas sem atualização. Priorizar baixa no app e conclusão de entrega.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "Mapeamos os pedidos *em rota* com vencimento limite para HOJE. Precisamos da baixa ou conclusão de entrega ainda neste turno para evitar estouro de SLA.",
            "",
            "⚠️ *AJs prioritários:*",
        ]
    )
    for courier, group in due_today.groupby("courier", dropna=False, sort=True):
        driver = _fmt_cell(courier, "SEM MOTORISTA")
        lines.append(f"\n*{driver}*")
        for _, row in group.iterrows():
            waybill = _fmt_cell(row.get("waybill"), "SEM_WAYBILL")
            status = _fmt_cell(row.get("status"), "Em rota de entrega")
            lines.append(f"{waybill}\t{status}\t{driver}")
    _append_waybill_copy_block(lines, due_today, title="AJs vence hoje para copiar")
    lines.extend(
        [
            "",
            "📌 Ação necessária: alinhamento imediato com os motoristas para garantia de baixa no app até o fechamento. Contamos com o empenho da base para fechar o dia sem pendências! 🚚📦",
        ]
    )
    return "\n".join(lines)


def build_lastmile_routine(
    df: pd.DataFrame,
    *,
    as_of: pd.Timestamp | str | date | None = None,
    region: str | None = "Regional Sul",
    inbound_from: pd.Timestamp | str | date | None = None,
    inbound_to: pd.Timestamp | str | date | None = None,
    only_bases: list[str] | None = None,
) -> LastMileRoutine:
    """Monta backlog e exceções de SLA do dia, sem mutar o DataFrame de origem.

    ``due_at`` determina o vencimento. A fila urgente exige status contendo
    ``rota`` e dia de vencimento igual ao dia de referência. Assim, pedidos
    já entregues, devolvidos ou somente atrasados não são cobrados como "hoje".
    """
    if "status" not in df.columns or "due_at" not in df.columns:
        raise ValueError("A rotina exige as colunas 'status' e 'due_at'.")

    reference = pd.Timestamp(as_of or pd.Timestamp.now()).normalize()
    work = df.copy()
    if region and "region" in work.columns:
        work = work.loc[work["region"].astype("string").str.strip().eq(region)].copy()
    if inbound_from is not None or inbound_to is not None:
        if "base_inbound" not in work.columns:
            raise ValueError("O filtro de inbound exige a coluna 'base_inbound'.")
        inbound = pd.to_datetime(work["base_inbound"], errors="coerce")
        if inbound_from is not None:
            work = work.loc[inbound.ge(pd.Timestamp(inbound_from).normalize())].copy()
            inbound = inbound.loc[work.index]
        if inbound_to is not None:
            end = pd.Timestamp(inbound_to)
            # Datas sem horário representam o fim daquele dia operacional.
            if end == end.normalize():
                end += pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            work = work.loc[inbound.le(end)].copy()

    base_col = _base_column(work)
    work["_base"] = _clean_base(work[base_col])
    if only_bases:
        allowed = {str(b).strip() for b in only_bases}
        work = work.loc[work["_base"].isin(allowed)].copy()
    open_mask = _is_open(work)
    status = work["status"].astype("string")
    operational_mask = pd.Series(False, index=work.index)
    for hint in OPEN_STATUS_HINTS:
        operational_mask |= _contains(status, hint)
    backlog = work.loc[open_mask & operational_mask].copy()

    due_day = pd.to_datetime(work["due_at"], errors="coerce").dt.normalize()
    today_mask = due_day.eq(reference)
    route_today = work.loc[open_mask & today_mask & _contains(status, ROUTE_STATUS_HINT)].copy()
    floor_today = work.loc[open_mask & today_mask & _contains(status, FLOOR_STATUS_HINT)].copy()

    backlog = _sort_queue(backlog)
    route_today = _sort_queue(route_today)
    floor_today = _sort_queue(floor_today)
    summary = _backlog_summary(backlog)

    bases = sorted(set(backlog["_base"]).union(set(route_today["_base"])))
    messages = {
        base: _backlog_message_for_base(
            base,
            backlog.loc[backlog["_base"].eq(base)],
            reference,
        )
        for base in bases
        if not backlog.loc[backlog["_base"].eq(base)].empty
    }
    preventivo_messages = {
        base: _preventivo_message_for_base(
            base,
            route_today.loc[route_today["_base"].eq(base)],
            int((backlog["_base"] == base).sum()),
            reference,
        )
        for base in bases
    }
    return LastMileRoutine(
        as_of=reference,
        regional_backlog=_to_detail(backlog),
        base_summary=summary,
        due_today_route=_to_detail(route_today),
        due_today_floor=_to_detail(floor_today),
        messages=messages,
        preventivo_messages=preventivo_messages,
    )
