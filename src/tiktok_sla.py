"""Cobrança TikTok — SLA last mile (inbound + D0/D+N)."""

from __future__ import annotations

import pandas as pd

from .backlog_lastmile import _append_grouped_lines, _append_waybill_copy_block, _fmt_cell, _is_route_status
from .preventivo_lastmile import RESUMO_DEPOIS, RESUMO_FORA, RESUMO_VENCE_HOJE, apply_inbound_resumo

CLIENT_NEEDLE = "tiktok"


def filter_tiktok(df: pd.DataFrame) -> pd.DataFrame:
    client = df.get("client", pd.Series("", index=df.index)).astype("string")
    merchant = df.get("merchant", pd.Series("", index=df.index)).astype("string")
    mask = client.str.contains(CLIENT_NEEDLE, case=False, na=False) | merchant.str.contains(
        CLIENT_NEEDLE, case=False, na=False
    )
    return df.loc[mask].copy()


def _line_aj(row: pd.Series, *, extra: str) -> str:
    return (
        f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
        f"{_fmt_cell(row.get('dest_city'), '—')}\t"
        f"{_fmt_cell(row.get('courier'), 'sem motorista')}\t"
        f"{extra}"
    )


def tiktok_message(base: str, group: pd.DataFrame, as_of: pd.Timestamp) -> str:
    date_label = as_of.strftime("%d/%m/%Y")
    fora = group.loc[group["resumo"].eq(RESUMO_FORA)].copy()
    hoje = group.loc[group["resumo"].eq(RESUMO_VENCE_HOJE)].copy()
    depois = int(group["resumo"].eq(RESUMO_DEPOIS).sum())
    if "dias_fora" in fora.columns:
        fora = fora.sort_values("dias_fora", ascending=False)
    hoje = hoje.sort_values(["status", "courier", "waybill"], na_position="last")

    lines = [
        "🎯 *TIKTOK – SLA HOJE (prioridade cliente)*",
        "",
        f"Base: *{base}* | {date_label}",
        f"TikTok na base: *{len(group)}*  ·  🚨 FORA: *{len(fora)}*  ·  VENCE HOJE: *{len(hoje)}*  ·  vence depois: {depois}",
        "",
        "Cliente: *tiktok logistics brazil ltda*",
        "Hoje o foco é o SLA deste cliente. Sem baixa no turno, vira estouro. Não misturar com o backlog geral.",
        "",
        "Até *18h*, para cada AJ abaixo: entregue / previsão de baixa / impedimento (1 linha).",
    ]

    if not fora.empty:
        lines.extend(["", f"🚨 *FORA DO PRAZO — {len(fora)} AJ(s)* (inbound + prazo da cidade)"])
        route = fora.loc[fora["status"].map(_is_route_status)]
        floor = fora.loc[~fora["status"].map(_is_route_status)]
        if not floor.empty:
            lines.append("\n_No piso / recebido / anomalia_")
            _append_grouped_lines(
                lines,
                floor,
                group_key="status",
                empty_group="SEM STATUS",
                line_fn=lambda row: _line_aj(row, extra=f"{int(row.get('dias_fora') or 0)}d fora"),
            )
        if not route.empty:
            lines.append("\n_Em rota_")
            _append_grouped_lines(
                lines,
                route,
                group_key="courier",
                empty_group="SEM MOTORISTA",
                line_fn=lambda row: _line_aj(row, extra=f"{int(row.get('dias_fora') or 0)}d fora"),
            )

    if not hoje.empty:
        lines.extend(["", f"🟢 *VENCE HOJE — {len(hoje)} AJ(s)* — encerrar no turno"])
        route = hoje.loc[hoje["status"].map(_is_route_status)]
        floor = hoje.loc[~hoje["status"].map(_is_route_status)]
        if not floor.empty:
            lines.append("\n_No piso_")
            _append_grouped_lines(
                lines,
                floor,
                group_key="dest_city",
                empty_group="SEM CIDADE",
                line_fn=lambda row: _line_aj(row, extra=_fmt_cell(row.get("status"), "piso")),
            )
        if not route.empty:
            lines.append("\n_Em rota_")
            _append_grouped_lines(
                lines,
                route,
                group_key="courier",
                empty_group="SEM MOTORISTA",
                line_fn=lambda row: _line_aj(row, extra=_fmt_cell(row.get("dest_city"), "—")),
            )

    acao = pd.concat([fora, hoje], ignore_index=True) if not fora.empty or not hoje.empty else fora
    _append_waybill_copy_block(lines, acao, title="AJs TikTok para copiar")
    lines.extend(
        [
            "",
            "📌 Sem resposta no AJ = tratar como parado. Cliente em evidência hoje — não deixar virar D+1.",
        ]
    )
    return "\n".join(lines)


def prepare_tiktok(
    tracking: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    prazo_lookup: pd.DataFrame,
    responsabilidade: pd.DataFrame,
) -> pd.DataFrame:
    tt = filter_tiktok(tracking)
    if tt.empty:
        return tt
    tt = tt.copy()
    tt["base"] = tt["last_point"].astype("string").str.strip() if "last_point" in tt.columns else "SEM_BASE"
    resp = responsabilidade.copy()
    resp["base"] = resp["base"].astype("string").str.strip()
    tt = tt.merge(resp, on="base", how="left")
    tt["responsavel"] = tt["responsavel"].fillna("SEM CARTEIRA").astype("string").str.strip()
    tt = apply_inbound_resumo(tt, prazo_lookup, as_of)
    venc = pd.to_datetime(tt["lm_vencimento"], errors="coerce").dt.normalize()
    tt["dias_fora"] = (pd.Timestamp(as_of).normalize() - venc).dt.days
    tt["dias_fora"] = tt["dias_fora"].where(tt["resumo"].eq(RESUMO_FORA), 0)
    return tt
