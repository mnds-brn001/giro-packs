"""Painel de ação diária — desvios de malha.

Cruza o ponto pré-alocado (malha oficial) com o ponto físico. A saída é
lista de tarefa: devolver / não entregar. Sem métrica de SLA na ponta.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .backlog_lastmile import _clean_base
from .loader import EMPTY_MARKERS

ACAO_DEVOLVER_HUB = "Devolver HUB"
ACAO_NAO_ENTREGAR = "Não entregar — devolver HUB"
ACAO_ENCAMINHAR = "Encaminhar ao pré-alocado"

HUB_POINT_RE = re.compile(r"(?:-H\d|H00|SP-RR|HUB)", re.IGNORECASE)

PRIORIDADE_CRITICA = "Crítica"
PRIORIDADE_ALTA = "Alta"
PRIORIDADE_MEDIA = "Média"

PAINEL_COLS = (
    "Waybill",
    "Base Atual",
    "Ponto Certo",
    "Ação Requerida",
    "Prioridade",
)


def _filled(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return series.notna() & ~text.isin(EMPTY_MARKERS) & text.ne("SEM_BASE")


def _physical_base(df: pd.DataFrame) -> pd.Series:
    last = df["last_point"] if "last_point" in df.columns else pd.Series(pd.NA, index=df.index)
    entry = df["entry_point"] if "entry_point" in df.columns else pd.Series(pd.NA, index=df.index)
    current = last.where(_filled(last), entry)
    return _clean_base(current)


def _is_hub_point(series: pd.Series) -> pd.Series:
    return series.astype("string").str.contains(HUB_POINT_RE, na=False)


def _acao(status: pd.Series, current: pd.Series) -> pd.Series:
    route = status.astype("string").str.contains("rota", case=False, na=False)
    at_hub = _is_hub_point(current)
    out = pd.Series(ACAO_DEVOLVER_HUB, index=status.index, dtype="string")
    out = out.mask(route, ACAO_NAO_ENTREGAR)
    out = out.mask(at_hub, ACAO_ENCAMINHAR)
    return out


def _prioridade(
    delay_days: pd.Series,
    due_at: pd.Series,
    inbound: pd.Series,
    as_of: pd.Timestamp,
) -> pd.Series:
    reference = pd.Timestamp(as_of).normalize()
    delay = pd.to_numeric(delay_days, errors="coerce")
    due = pd.to_datetime(due_at, errors="coerce").dt.normalize()
    parked = (reference - pd.to_datetime(inbound, errors="coerce").dt.normalize()).dt.days
    late = due.notna() & (due < reference)
    critica = (delay.fillna(0) >= 10) | (parked.fillna(0) >= 10)
    alta = late | (delay.fillna(0) > 0)
    out = pd.Series(PRIORIDADE_MEDIA, index=delay_days.index, dtype="string")
    out = out.mask(alta, PRIORIDADE_ALTA)
    out = out.mask(critica, PRIORIDADE_CRITICA)
    return out


def build_mesh_action(
    tracking: pd.DataFrame,
    *,
    as_of: pd.Timestamp | None = None,
    responsabilidade: pd.DataFrame | None = None,
    only_bases: list[str] | None = None,
    region: str | None = None,
) -> pd.DataFrame:
    """Abertos com pré ≠ ponto físico. Uma linha = uma movimentação."""
    as_of = pd.Timestamp(as_of or tracking.attrs.get("as_of") or pd.Timestamp.now())
    work = tracking.copy()
    if work.empty or "pre_point" not in work.columns:
        return pd.DataFrame(columns=list(PAINEL_COLS))

    if region and "region" in work.columns:
        work = work.loc[work["region"].astype("string").str.contains(region, case=False, na=False)]

    if "is_open" in work.columns:
        work = work.loc[work["is_open"].fillna(False)]
    elif "status" in work.columns:
        work = work.loc[~work["status"].astype("string").str.contains("entregue", case=False, na=False)]

    work["Base Atual"] = _physical_base(work)
    work["Ponto Certo"] = _clean_base(work["pre_point"])
    keep = (
        _filled(work["Base Atual"])
        & _filled(work["Ponto Certo"])
        & work["Base Atual"].ne(work["Ponto Certo"])
    )
    work = work.loc[keep]
    if only_bases:
        wanted = {str(b).strip() for b in only_bases}
        work = work.loc[work["Base Atual"].isin(wanted)]
    if work.empty:
        return pd.DataFrame(columns=list(PAINEL_COLS))

    status = work.get("status", pd.Series("", index=work.index))
    work["Ação Requerida"] = _acao(status, work["Base Atual"])
    work["Prioridade"] = _prioridade(
        work.get("delay_days", pd.Series(pd.NA, index=work.index)),
        work.get("due_at", pd.Series(pd.NaT, index=work.index)),
        work.get("base_inbound", pd.Series(pd.NaT, index=work.index)),
        as_of,
    )
    work["Waybill"] = work["waybill"].astype("string").str.strip()
    work["Cidade"] = work["dest_city"].astype("string").str.strip() if "dest_city" in work.columns else ""
    work["Status"] = status.astype("string").str.strip()

    if responsabilidade is not None and not responsabilidade.empty:
        resp = responsabilidade.copy()
        resp["base"] = resp["base"].astype("string").str.strip()
        owners = resp.drop_duplicates("base")
        work = work.merge(owners.rename(columns={"base": "Base Atual", "responsavel": "Responsável"}), on="Base Atual", how="left")
        dest = owners.rename(columns={"base": "Ponto Certo", "responsavel": "Dono do ponto certo"})
        work = work.merge(dest, on="Ponto Certo", how="left")
        work["Responsável"] = work["Responsável"].fillna("SEM CARTEIRA")
    else:
        work["Responsável"] = "SEM CARTEIRA"

    order = {PRIORIDADE_CRITICA: 0, PRIORIDADE_ALTA: 1, PRIORIDADE_MEDIA: 2}
    work["_ord"] = work["Prioridade"].map(order).fillna(9)
    work = work.sort_values(["_ord", "Base Atual", "Waybill"])
    return work.reset_index(drop=True)


def painel_acao(df: pd.DataFrame, *, com_filtro: bool = True) -> pd.DataFrame:
    """Folha que o supervisor abre — tarefa, não diagnóstico."""
    cols = list(PAINEL_COLS)
    if com_filtro:
        for extra in ("Cidade", "Responsável"):
            if extra in df.columns:
                cols.append(extra)
    present = [c for c in cols if c in df.columns]
    if df.empty:
        return pd.DataFrame(columns=present or list(PAINEL_COLS))
    out = df[present].copy()
    for col in out.columns:
        out[col] = out[col].astype("string")
    return out


@dataclass(frozen=True)
class MeshActionPack:
    as_of: pd.Timestamp
    detail: pd.DataFrame

    @property
    def painel(self) -> pd.DataFrame:
        return painel_acao(self.detail)

    def for_owner(self, owner: str) -> pd.DataFrame:
        if self.detail.empty or "Responsável" not in self.detail.columns:
            return painel_acao(self.detail.iloc[0:0])
        part = self.detail.loc[
            self.detail["Responsável"].astype("string").str.contains(owner, case=False, na=False)
        ]
        return painel_acao(part)


def write_mesh_excel(detail: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    painel = painel_acao(detail)
    resumo = pd.DataFrame()
    if not detail.empty and "Base Atual" in detail.columns:
        group_cols = [c for c in ("Responsável", "Base Atual") if c in detail.columns]
        resumo = (
            detail.groupby(group_cols, dropna=False)
            .agg(
                Pacotes=("Waybill", "size"),
                Critica=("Prioridade", lambda s: int(s.eq(PRIORIDADE_CRITICA).sum())),
            )
            .reset_index()
            .sort_values(["Critica", "Pacotes"], ascending=[False, False])
        )
    with pd.ExcelWriter(destination, engine="xlsxwriter") as writer:
        book = writer.book
        title = book.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#073B18"}
        )
        critica = book.add_format({"font_color": "#C00000", "bold": True})
        frames = {"Painel de acao": painel, "Resumo por base": resumo}
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            ws.freeze_panes(1, 0)
            if not frame.empty:
                ws.autofilter(0, 0, len(frame), max(len(frame.columns) - 1, 0))
            for idx, col in enumerate(frame.columns):
                ws.set_column(idx, idx, min(max(len(str(col)) + 4, 16), 36))
                ws.write(0, idx, col, title)
            if sheet == "Painel de acao" and "Prioridade" in frame.columns and not frame.empty:
                pri = list(frame.columns).index("Prioridade")
                ws.set_column(pri, pri, 14, critica)


def action_kpis(detail: pd.DataFrame) -> dict[str, int]:
    if detail.empty or "Ação Requerida" not in detail.columns:
        return {"total": 0, "devolver": 0, "nao_entregar": 0, "encaminhar": 0, "critica": 0, "piso": 0}
    devolver = int(detail["Ação Requerida"].eq(ACAO_DEVOLVER_HUB).sum())
    nao = int(detail["Ação Requerida"].eq(ACAO_NAO_ENTREGAR).sum())
    enc = int(detail["Ação Requerida"].eq(ACAO_ENCAMINHAR).sum())
    crit = int(detail["Prioridade"].eq(PRIORIDADE_CRITICA).sum()) if "Prioridade" in detail.columns else 0
    return {
        "total": len(detail),
        "devolver": devolver,
        "nao_entregar": nao,
        "encaminhar": enc,
        "critica": crit,
        "piso": devolver + nao,
    }


def painel_excel_bytes(detail: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    painel = painel_acao(detail)
    resumo = pd.DataFrame()
    if not detail.empty and "Base Atual" in detail.columns:
        group_cols = [c for c in ("Responsável", "Base Atual") if c in detail.columns]
        resumo = (
            detail.groupby(group_cols, dropna=False)
            .agg(
                Pacotes=("Waybill", "size"),
                Critica=("Prioridade", lambda s: int(s.eq(PRIORIDADE_CRITICA).sum())),
            )
            .reset_index()
            .sort_values(["Critica", "Pacotes"], ascending=[False, False])
        )
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        painel.to_excel(writer, sheet_name="Painel de acao", index=False)
        resumo.to_excel(writer, sheet_name="Resumo por base", index=False)
    return buf.getvalue()


def render_mesh_action_block(
    tracking: pd.DataFrame,
    *,
    as_of: pd.Timestamp | None = None,
    responsabilidade: pd.DataFrame | None = None,
    region: str | None = None,
    key_prefix: str = "malha",
    excel_name: str = "painel_malha.xlsx",
) -> pd.DataFrame:
    """Bloco Streamlit: lista de tarefa no topo, sem métrica de SLA."""
    import streamlit as st

    if responsabilidade is None:
        try:
            from .ingest import resolve_prazo
            from .preventivo_lastmile import load_prazo_tables

            responsabilidade = load_prazo_tables(resolve_prazo())[1]
        except FileNotFoundError:
            responsabilidade = None

    detail = build_mesh_action(
        tracking,
        as_of=as_of,
        responsabilidade=responsabilidade,
        region=region,
    )
    kpis = action_kpis(detail)
    st.markdown("#### Painel de ação diária")
    st.caption(
        "Filtre a base atual. A ponta só executa a movimentação — "
        "não interpretar SLA. Quem tem o pacote na mão devolve; o ponto certo não é cobrado no OTD."
    )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tarefas", f"{kpis['total']:,}".replace(",", "."))
    m2.metric("Piso (devolver)", f"{kpis['piso']:,}".replace(",", "."))
    m3.metric("Encaminhar no HUB", f"{kpis['encaminhar']:,}".replace(",", "."))
    m4.metric("Crítica", f"{kpis['critica']:,}".replace(",", "."))

    if detail.empty:
        st.success("Nenhum desvio aberto para movimentar neste recorte.")
        return detail

    f1, f2, f3, f4 = st.columns(4)
    bases = sorted(detail["Base Atual"].dropna().astype(str).unique())
    acoes = sorted(detail["Ação Requerida"].dropna().astype(str).unique())
    pris = [PRIORIDADE_CRITICA, PRIORIDADE_ALTA, PRIORIDADE_MEDIA]
    pris = [p for p in pris if p in set(detail["Prioridade"].astype(str))]
    owners = (
        sorted(detail["Responsável"].dropna().astype(str).unique())
        if "Responsável" in detail.columns
        else []
    )
    with f1:
        sel_base = st.multiselect("Base atual", bases, key=f"{key_prefix}_base")
    with f2:
        sel_acao = st.multiselect("Ação", acoes, key=f"{key_prefix}_acao")
    with f3:
        sel_pri = st.multiselect("Prioridade", pris, key=f"{key_prefix}_pri")
    with f4:
        sel_owner = st.multiselect("Responsável", owners, key=f"{key_prefix}_owner") if owners else []
    only_piso = st.checkbox("Só piso (esconder HUB)", value=False, key=f"{key_prefix}_piso")

    recorte = detail
    if only_piso:
        recorte = recorte.loc[recorte["Ação Requerida"].ne(ACAO_ENCAMINHAR)]
    if sel_base:
        recorte = recorte.loc[recorte["Base Atual"].isin(sel_base)]
    if sel_acao:
        recorte = recorte.loc[recorte["Ação Requerida"].isin(sel_acao)]
    if sel_pri:
        recorte = recorte.loc[recorte["Prioridade"].isin(sel_pri)]
    if sel_owner and "Responsável" in recorte.columns:
        recorte = recorte.loc[recorte["Responsável"].isin(sel_owner)]

    painel = painel_acao(recorte)
    st.dataframe(painel, use_container_width=True, hide_index=True, height=360)
    st.download_button(
        "Baixar painel de ação (Excel)",
        data=painel_excel_bytes(recorte),
        file_name=excel_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key_prefix}_xlsx",
    )
    return recorte
