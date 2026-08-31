"""Devolução ao hub superior — aging, copies por base e painel HTML.

O status "saiu de [BASE] e foi devolvido ao centro de trânsito de [HUB]"
é expedição registrada no sistema. Não prova chegada no hub nem saída física.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .backlog_lastmile import _clean_base, _fmt_cell
from .columns import COL

TRACK_RE = re.compile(
    r"saiu de \[(?P<origin>[^\]]+)\] e foi devolvido ao centro de trânsito de \[(?P<hub>[^\]]+)\]",
    re.IGNORECASE,
)

BUCKET_GREEN = "🟢 1 a 3 dias"
BUCKET_YELLOW = "🟡 4 a 6 dias"
BUCKET_ORANGE = "🟠 7 a 9 dias"
BUCKET_RED = "🚨 +10 dias - Fora do prazo"

MOTIVO_ACAO = {
    "endereço": "Contactar vendedor/cliente, decidir reenvio ou devolução e informar prazo.",
    "destinário": "Contactar vendedor/cliente, decidir reenvio ou devolução e informar prazo.",
    "não pertence": "Separar e despachar ao hub no próximo roteiro — erro de malha, prioridade alta.",
    "nao pertence": "Separar e despachar ao hub no próximo roteiro — erro de malha, prioridade alta.",
    "perda": "Confirmar que nada ficou físico na base; informar protocolo de indenização.",
    "indenização": "Confirmar que nada ficou físico na base; informar protocolo de indenização.",
    "recusado": "Confirmar despacho ao hub e se a recusa está evidenciada no PDA.",
    "avariado": "Confirmar evidência de avaria e despacho ao hub / fluxo de retorno.",
    "ausente": "Confirmar tentativas e despacho ao hub se esgotou o SOP.",
    "ciclo": "Devolução por ciclo excedido — despachar ao hub sem reter na base.",
    "risco": "Registrar evidência de área de risco e encaminhar ao hub.",
    "fechado": "Confirmar horário comercial / nova tentativa e, se esgotado, despacho ao hub.",
    "interceptado": "Confirmar se a interceptação foi concluída e se o volume já saiu ao hub.",
}

HUB_RETURN_KEYS = (
    "waybill",
    "status",
    "last_track_time",
    "last_track",
    "last_point",
    "issue_reason",
    "dest_city",
    "dest_state",
    "pre_point",
    "entry_point",
    "first_return_at",
)


@dataclass(frozen=True)
class HubReturnRoutine:
    as_of: pd.Timestamp
    detail: pd.DataFrame
    summary: pd.DataFrame
    messages: dict[str, str]
    html: str


def age_bucket(days: float | int | None) -> str:
    """Faixas do painel: 0–3 / 4–6 / 7–9 / ≥10."""
    if days is None or pd.isna(days):
        return BUCKET_GREEN
    n = int(days)
    if n >= 10:
        return BUCKET_RED
    if n >= 7:
        return BUCKET_ORANGE
    if n >= 4:
        return BUCKET_YELLOW
    return BUCKET_GREEN


def parse_hub_transfer(track: object) -> tuple[str, str]:
    text = "" if track is None or (isinstance(track, float) and pd.isna(track)) else str(track)
    match = TRACK_RE.search(text)
    if not match:
        return "SEM_ORIGEM", "SEM_HUB"
    return match.group("origin").strip(), match.group("hub").strip()


def motivo_acao(motivo: object) -> str:
    text = _fmt_cell(motivo, "").casefold()
    for needle, action in MOTIVO_ACAO.items():
        if needle in text:
            return action
    return "Informar se já saiu fisicamente ao hub e a tratativa em curso."


def _read_excel(path: Path, *, sheet_name=0) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet_name, engine="calamine")
    except (ValueError, ImportError, Exception):
        return pd.read_excel(path, sheet_name=sheet_name)


def load_hub_return_source(source: str | Path) -> pd.DataFrame:
    path = Path(source)
    try:
        names = pd.ExcelFile(path, engine="calamine").sheet_names
    except (ValueError, ImportError, Exception):
        names = pd.ExcelFile(path).sheet_names
    sheet = "DADOS" if "DADOS" in names else names[0]
    raw = _read_excel(path, sheet_name=sheet)
    reverse = {label: key for key, label in COL.items() if key in HUB_RETURN_KEYS}
    work = raw.rename(columns=reverse)
    extra = {
        "responsavel": next((c for c in raw.columns if "RESPONSABILIDADE" in str(c).upper()), None),
        "dias_status": next((c for c in raw.columns if "Dias desde" in str(c)), None),
        "situacao_origem": next((c for c in raw.columns if str(c).strip() in {"Situação", "Situacao"}), None),
    }
    for key, col in extra.items():
        if col is not None:
            work[key] = raw[col]
    if "waybill" not in work.columns:
        work["waybill"] = raw.iloc[:, 0]
    work["waybill"] = work["waybill"].astype("string").str.strip()
    if "last_track_time" in work.columns:
        work["last_track_time"] = pd.to_datetime(work["last_track_time"], errors="coerce")
    return work


def _days_in_status(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    if "dias_status" in df.columns:
        days = pd.to_numeric(df["dias_status"], errors="coerce")
        if days.notna().any():
            return days
    if "last_track_time" in df.columns:
        delta = as_of.normalize() - pd.to_datetime(df["last_track_time"], errors="coerce").dt.normalize()
        return delta.dt.days
    return pd.Series(0, index=df.index)


def hub_return_message(base: str, group: pd.DataFrame, as_of: pd.Timestamp) -> str:
    date_label = as_of.strftime("%d/%m/%Y")
    hubs = sorted({h for h in group["hub"].astype(str) if h and h != "SEM_HUB"})
    hub_label = ", ".join(hubs) if hubs else "HUB SUPERIOR"
    buckets = group["bucket"].value_counts()
    n_red = int(buckets.get(BUCKET_RED, 0))
    n_orange = int(buckets.get(BUCKET_ORANGE, 0))
    n_yellow = int(buckets.get(BUCKET_YELLOW, 0))
    n_green = int(buckets.get(BUCKET_GREEN, 0))

    lines = [
        "🔄 *DEVOLUÇÃO AO HUB SUPERIOR – COBRANÇA DE TRATATIVA*",
        "",
        f"Base: *{base}*  →  Hub: *{hub_label}*",
        f"Data-base: *{date_label}*",
        f"Volume: *{len(group)}*  |  🚨+10d: *{n_red}*  ·  🟠7–9d: {n_orange}  ·  🟡4–6d: {n_yellow}  ·  🟢1–3d: {n_green}",
        "",
        "O status *“saiu da base e foi devolvido ao centro de trânsito”* é expedição no sistema. "
        "Não prova que o pacote já saiu fisicamente nem que o hub já recebeu.",
        "",
        "Precisamos de retorno *até amanhã 18h* para cada AJ 🟠/🚨:",
        "1) Já despachado fisicamente ao hub? SIM → data/hora + manifesto. NÃO → motivo + previsão.",
        "2) Ainda físico na base? Waybill + retenção + ação em curso.",
    ]

    critical = group.loc[group["bucket"].isin([BUCKET_RED, BUCKET_ORANGE])].copy()
    critical = critical.sort_values(["days", "issue_reason", "waybill"], ascending=[False, True, True])
    if critical.empty:
        lines.extend(
            [
                "",
                "Não há pacote em 🟠7–9d nem 🚨+10d nesta extração. Manter giro do restante em 24–48h.",
            ]
        )
    else:
        for bucket in (BUCKET_RED, BUCKET_ORANGE):
            subset = critical.loc[critical["bucket"].eq(bucket)]
            if subset.empty:
                continue
            lines.append("")
            lines.append(f"*{bucket}*")
            for motivo, pack in subset.groupby("issue_reason", dropna=False, sort=True):
                label = _fmt_cell(motivo, "SEM MOTIVO")
                lines.append(f"\n_{label}_")
                lines.append(f"Ação: {motivo_acao(label)}")
                for _, row in pack.iterrows():
                    ts = row.get("last_track_time")
                    when = pd.Timestamp(ts).strftime("%d/%m") if pd.notna(ts) else "s/data"
                    lines.append(
                        f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
                        f"{when}\t"
                        f"{int(row['days'])}d\t"
                        f"{_fmt_cell(row.get('dest_city'), '—')}"
                    )

    yellow = group.loc[group["bucket"].eq(BUCKET_YELLOW)]
    if not yellow.empty:
        lines.append("")
        lines.append(f"*🟡 4 a 6 dias* — {len(yellow)} pacote(s). Despachar antes de virar +7d.")
        for _, row in yellow.sort_values("days", ascending=False).iterrows():
            lines.append(
                f"{_fmt_cell(row.get('waybill'), 'SEM_WAYBILL')}\t"
                f"{_fmt_cell(row.get('issue_reason'), 'SEM MOTIVO')}\t"
                f"{int(row['days'])}d"
            )

    green_motivos = (
        group.loc[group["bucket"].eq(BUCKET_GREEN), "issue_reason"]
        .fillna("SEM MOTIVO")
        .astype(str)
        .value_counts()
    )
    if not green_motivos.empty:
        lines.append("")
        lines.append(f"*🟢 1 a 3 dias* — {int(green_motivos.sum())} pacote(s) no prazo de giro:")
        for motivo, qtd in green_motivos.items():
            lines.append(f"• {motivo}: {int(qtd)}")

    lines.extend(
        [
            "",
            "📌 Sem evidência de manifesto/roteiro, tratar como *não enviado*. Casos sem resposta em 24h serão escalonados.",
        ]
    )
    return "\n".join(lines)


def _summary_table(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=["Base", "Hub", BUCKET_GREEN, BUCKET_YELLOW, BUCKET_ORANGE, BUCKET_RED, "Total Geral"]
        )
    work = detail.copy()
    flags = pd.DataFrame(
        {
            "Base": work["base"],
            "Hub": work["hub"],
            BUCKET_GREEN: (work["bucket"] == BUCKET_GREEN).astype(int),
            BUCKET_YELLOW: (work["bucket"] == BUCKET_YELLOW).astype(int),
            BUCKET_ORANGE: (work["bucket"] == BUCKET_ORANGE).astype(int),
            BUCKET_RED: (work["bucket"] == BUCKET_RED).astype(int),
        }
    )
    grouped = flags.groupby(["Base", "Hub"], as_index=False).sum()
    grouped["Total Geral"] = grouped.iloc[:, 2:].sum(axis=1)
    return grouped.sort_values([BUCKET_RED, "Total Geral"], ascending=[False, False]).reset_index(drop=True)


def render_hub_return_html(routine: "HubReturnRoutine") -> str:
    summary = routine.summary
    detail = routine.detail
    n = len(detail)
    n_red = int((detail["bucket"] == BUCKET_RED).sum()) if n else 0
    n_orange = int((detail["bucket"] == BUCKET_ORANGE).sum()) if n else 0
    n_yellow = int((detail["bucket"] == BUCKET_YELLOW).sum()) if n else 0
    n_green = int((detail["bucket"] == BUCKET_GREEN).sum()) if n else 0
    date_label = routine.as_of.strftime("%d/%m/%Y")

    def esc(val) -> str:
        return html.escape(_fmt_cell(val, "—"))

    motivo_rows = []
    if n and "issue_reason" in detail.columns:
        for motivo, qtd in detail["issue_reason"].fillna("SEM MOTIVO").astype(str).value_counts().items():
            motivo_rows.append(f"<tr><td>{esc(motivo)}</td><td>{int(qtd)}</td></tr>")

    rows_html = []
    for _, row in summary.iterrows():
        rows_html.append(
            "<tr>"
            f"<td><code>{esc(row['Base'])}</code></td>"
            f"<td><code>{esc(row['Hub'])}</code></td>"
            f"<td>{int(row[BUCKET_GREEN])}</td>"
            f"<td>{int(row[BUCKET_YELLOW])}</td>"
            f"<td class='warn'>{int(row[BUCKET_ORANGE])}</td>"
            f"<td class='crit'>{int(row[BUCKET_RED])}</td>"
            f"<td>{int(row['Total Geral'])}</td>"
            "</tr>"
        )

    base_blocks = []
    for base in summary["Base"].astype(str):
        group = detail.loc[detail["base"].eq(base)].sort_values("days", ascending=False)
        hub = esc(group["hub"].iloc[0]) if not group.empty else "—"
        crit = group.loc[group["bucket"].isin([BUCKET_RED, BUCKET_ORANGE])]
        items = []
        for _, row in crit.iterrows():
            items.append(
                "<li>"
                f"<code>{esc(row['waybill'])}</code> · {esc(row['bucket'])} · "
                f"{int(row['days'])}d · {esc(row['issue_reason'])} · {esc(row['dest_city'])}"
                "</li>"
            )
        if not items:
            items.append("<li>Sem 🟠/🚨 nesta base.</li>")
        base_blocks.append(
            f"<section class='base-card' id='{html.escape(base)}'>"
            f"<h3><code>{esc(base)}</code> → <code>{hub}</code> · {len(group)} pct</h3>"
            f"<ul>{''.join(items)}</ul>"
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Devolução ao Hub Superior · {date_label}</title>
<style>
  :root {{
    --bg: #04140f;
    --panel: #073b18;
    --panel-2: #0b4a20;
    --line: #14532d;
    --paper: #e8f5e9;
    --mist: #9cbc9a;
    --accent: #86efac;
    --warn: #fbbf24;
    --crit: #f87171;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0;
    background: radial-gradient(900px 500px at 10% -10%, rgba(22,163,74,.18), transparent 50%),
                linear-gradient(180deg, #02110c 0%, var(--bg) 40%, #062015 100%);
    color: var(--paper);
    font-family: "DM Sans", "Segoe UI", sans-serif;
  }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 64px; }}
  h1 {{ font-size: 1.7rem; margin: 0 0 .35rem; letter-spacing: -0.03em; }}
  h1 span {{ color: var(--accent); font-weight: 500; }}
  .sub {{ color: var(--mist); margin: 0 0 1.4rem; }}
  .kpis {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 22px; }}
  .kpi {{ background: var(--panel); border: 1px solid var(--line); padding: 12px 14px; }}
  .kpi .l {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--mist); }}
  .kpi .v {{ font-size: 1.6rem; font-weight: 700; margin-top: 4px; }}
  .kpi.crit .v {{ color: var(--crit); }}
  .kpi.warn .v {{ color: var(--warn); }}
  table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); }}
  th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--line); font-size: .88rem; }}
  th {{ background: #062d14; color: var(--accent); font-weight: 600; }}
  td.crit {{ color: var(--crit); font-weight: 700; }}
  td.warn {{ color: var(--warn); font-weight: 700; }}
  code {{ font-family: "IBM Plex Mono", Consolas, monospace; color: var(--accent); }}
  .note {{
    border-left: 4px solid var(--accent);
    background: rgba(7,59,24,.7);
    padding: 12px 14px;
    margin: 18px 0;
    color: var(--mist);
    font-size: .92rem;
  }}
  .base-card {{
    background: var(--panel);
    border: 1px solid var(--line);
    padding: 14px 16px;
    margin: 12px 0;
  }}
  .base-card h3 {{ margin: 0 0 8px; font-size: 1rem; }}
  .base-card ul {{ margin: 0; padding-left: 18px; color: var(--mist); }}
  .base-card li {{ margin: 4px 0; }}
  @media (max-width: 900px) {{ .kpis {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Devolução ao Hub Superior <span>— cobrança operacional</span></h1>
  <p class="sub">Data-base {esc(date_label)} · status agregado = expedição no sistema, não prova de chegada no hub.</p>
  <div class="kpis">
    <div class="kpi"><div class="l">Pacotes</div><div class="v">{n}</div></div>
    <div class="kpi crit"><div class="l">🚨 +10 dias</div><div class="v">{n_red}</div></div>
    <div class="kpi warn"><div class="l">🟠 7 a 9 dias</div><div class="v">{n_orange}</div></div>
    <div class="kpi"><div class="l">🟡 4 a 6 dias</div><div class="v">{n_yellow}</div></div>
    <div class="kpi"><div class="l">🟢 1 a 3 dias</div><div class="v">{n_green}</div></div>
  </div>
  <div class="note">
    Cobrar da base: (1) despacho físico ao hub com manifesto; (2) o que ainda está no piso;
    (3) tratativa por motivo — endereço errado, não pertence à base, recusa, avaria, ausente, ciclo excedido, perda/indenização.
  </div>
  <h2 style="margin:18px 0 8px;color:var(--accent);font-size:1rem;letter-spacing:.08em;text-transform:uppercase;">Motivos</h2>
  <table style="margin-bottom:22px;">
    <thead><tr><th>Motivo da ocorrência</th><th>Qtd</th></tr></thead>
    <tbody>{''.join(motivo_rows) if motivo_rows else '<tr><td colspan="2">Sem dados</td></tr>'}</tbody>
  </table>
  <h2 style="margin:18px 0 8px;color:var(--accent);font-size:1rem;letter-spacing:.08em;text-transform:uppercase;">Por base</h2>
  <table>
    <thead>
      <tr>
        <th>Base</th><th>Hub</th>
        <th>🟢 1–3</th><th>🟡 4–6</th><th>🟠 7–9</th><th>🚨 +10</th><th>Total</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
  <h2 style="margin-top:28px;color:var(--accent);font-size:1rem;letter-spacing:.08em;text-transform:uppercase;">Críticos por base</h2>
  {''.join(base_blocks)}
</div>
</body>
</html>
"""


def build_hub_return_routine(
    df: pd.DataFrame,
    *,
    as_of: pd.Timestamp | str | date | None = None,
    only_bases: list[str] | None = None,
) -> HubReturnRoutine:
    reference = pd.Timestamp(as_of or pd.Timestamp.now()).normalize()
    work = df.copy()
    parsed = work.get("last_track", pd.Series("", index=work.index)).map(parse_hub_transfer)
    work["origin"] = parsed.map(lambda t: t[0])
    work["hub"] = parsed.map(lambda t: t[1])
    if "last_point" in work.columns:
        point = work["last_point"].astype("string")
        missing = point.isna() | point.str.strip().isin(["", "--", "nan", "None", "NaT", "<NA>"])
        work["base"] = _clean_base(point.where(~missing, work["origin"]))
    else:
        work["base"] = _clean_base(work["origin"])
    if only_bases:
        allowed = {str(b).strip() for b in only_bases}
        work = work.loc[work["base"].isin(allowed)].copy()
    work["days"] = _days_in_status(work, reference).fillna(0)
    work["bucket"] = work["days"].map(age_bucket)
    if "issue_reason" not in work.columns:
        work["issue_reason"] = "SEM MOTIVO"

    detail_cols = [
        c
        for c in (
            "base",
            "hub",
            "origin",
            "waybill",
            "status",
            "last_track",
            "last_track_time",
            "issue_reason",
            "dest_city",
            "dest_state",
            "first_return_at",
            "days",
            "bucket",
            "responsavel",
        )
        if c in work.columns
    ]
    detail = work[detail_cols].sort_values(["days", "base", "waybill"], ascending=[False, True, True]).reset_index(drop=True)
    summary = _summary_table(detail)
    messages = {
        base: hub_return_message(base, group, reference)
        for base, group in detail.groupby("base", dropna=False, sort=True)
    }
    routine = HubReturnRoutine(
        as_of=reference,
        detail=detail,
        summary=summary,
        messages=messages,
        html="",
    )
    return HubReturnRoutine(
        as_of=routine.as_of,
        detail=routine.detail,
        summary=routine.summary,
        messages=routine.messages,
        html=render_hub_return_html(routine),
    )
