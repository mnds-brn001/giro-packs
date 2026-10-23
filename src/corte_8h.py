"""Diagnóstico do corte D+1: cidade D0 com inbound depois das 08:00.

O preventivo já aplica a regra em ``apply_inbound_resumo``. Este módulo só
mede o antes e o depois e monta o HTML. O pack do dia ainda não chama daqui.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .preventivo_lastmile import FORA_MIN_DAYS, apply_inbound_resumo

_RESUMO = {
    "FORA DO PRAZO / 截止日期后": "FORA",
    "VENCE HOJE / 赢在今天": "HOJE",
    "VENCE DEPOIS / 稍後獲勝": "DEPOIS",
}


@dataclass(frozen=True)
class CorteReport:
    as_of: pd.Timestamp
    carteira: int
    sem: dict[str, int]
    com: dict[str, int]
    mais9_sem: int
    mais9_com: int
    corte: int
    d0_cedo: int
    chegou_hoje: int
    chegou_ontem: int
    segue_fora: int
    saiu_mais9: int
    status: list[tuple[str, int]]
    horas: list[dict[str, int | str]]
    donos: list[dict[str, int | str]]
    bases: list[dict[str, int | str]]


def _bool(series: pd.Series) -> pd.Series:
    """Máscara numpy. ``corte_8h`` tem NA e o ``&`` com BooleanDtype zera a conta."""
    return series.fillna(False).astype(bool)


def _pt(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def _count(series: pd.Series, label: str) -> int:
    return int(series.eq(label).sum())


def build_corte_report(
    tracking: pd.DataFrame,
    prazo_lookup: pd.DataFrame,
    responsabilidade: pd.DataFrame,
    as_of: pd.Timestamp,
) -> CorteReport:
    """Carteira do prazo, no último ponto. Compara o vencimento com e sem o corte."""
    resp = responsabilidade.copy()
    resp["base"] = resp["base"].astype(str).str.strip()
    resp = resp.drop_duplicates("base")
    work = tracking.copy()
    point = work["last_point"] if "last_point" in work.columns else work.get("entry_point")
    if point is None:
        raise ValueError("Extração sem último ponto de rastreio.")
    work["base"] = point.astype(str).str.strip()
    work = work.loc[work["base"].isin(set(resp["base"]))].copy()
    work = apply_inbound_resumo(work, prazo_lookup, as_of)
    work = work.merge(resp, on="base", how="left")
    work["responsavel"] = work["responsavel"].fillna("SEM CARTEIRA")

    reference = pd.Timestamp(as_of).normalize()
    inbound_ts = pd.to_datetime(work["base_inbound"], errors="coerce")
    inbound = inbound_ts.dt.normalize()
    venc_sem = inbound + pd.to_timedelta(work["lm_dias"].fillna(0), unit="D")
    sem = pd.Series("DEPOIS", index=work.index)
    sem = sem.mask(venc_sem < reference, "FORA")
    sem = sem.mask(venc_sem.eq(reference), "HOJE")
    sem = sem.mask(inbound.isna() | work["lm_dias"].isna(), "SEM")
    com = work["resumo"].map(_RESUMO).fillna("SEM")
    dias_sem = (reference - venc_sem).dt.days
    mais9_sem = _bool(sem.eq("FORA") & dias_sem.ge(FORA_MIN_DAYS))
    mais9_com = _bool(com.eq("FORA") & work["dias_fora"].ge(FORA_MIN_DAYS))
    corte = _bool(work["corte_8h"].eq("D+1"))
    d0 = _bool(work["lm_dias"].eq(0))
    idade = (reference - inbound).dt.days
    chegou_hoje = corte & idade.eq(0)
    chegou_ontem = corte & idade.eq(1)
    segue_fora = corte & com.eq("FORA")
    saiu_mais9 = corte & mais9_sem & ~mais9_com

    work = work.assign(
        _hoje=chegou_hoje,
        _ontem=chegou_ontem,
        _fora=segue_fora,
        _mais9=saiu_mais9,
        _hora=inbound_ts.dt.hour,
    )
    hit = work.loc[corte]

    def pack(frame: pd.DataFrame) -> dict[str, int | str]:
        return {
            "corte": int(len(frame)),
            "hoje": int(frame["_hoje"].sum()),
            "ontem": int(frame["_ontem"].sum()),
            "fora": int(frame["_fora"].sum()),
            "mais9": int(frame["_mais9"].sum()),
        }

    donos = []
    for nome, group in hit.groupby("responsavel", dropna=False):
        row = pack(group)
        row["nome"] = str(nome)
        donos.append(row)
    donos.sort(key=lambda row: int(row["hoje"]), reverse=True)

    bases = []
    for (base, nome), group in hit.groupby(["base", "responsavel"], dropna=False):
        row = pack(group)
        row["base"] = str(base)
        row["nome"] = str(nome)
        bases.append(row)
    bases.sort(key=lambda row: int(row["corte"]), reverse=True)

    horas = []
    for hour in range(24):
        mask = d0 & work["_hora"].eq(hour)
        if not bool(mask.any()):
            continue
        ficou = int((mask & ~corte).sum())
        virou = int((mask & corte).sum())
        horas.append({"hora": f"{hour:02d}h", "cedo": ficou, "corte": virou})

    status = [
        (str(nome), int(qtd))
        for nome, qtd in hit["status"].fillna("sem status").astype(str).value_counts().items()
    ]
    return CorteReport(
        as_of=reference,
        carteira=len(work),
        sem={label: _count(sem, label) for label in ("HOJE", "FORA", "DEPOIS")},
        com={label: _count(com, label) for label in ("HOJE", "FORA", "DEPOIS")},
        mais9_sem=int(mais9_sem.sum()),
        mais9_com=int(mais9_com.sum()),
        corte=int(corte.sum()),
        d0_cedo=int((d0 & ~corte).sum()),
        chegou_hoje=int(chegou_hoje.sum()),
        chegou_ontem=int(chegou_ontem.sum()),
        segue_fora=int(segue_fora.sum()),
        saiu_mais9=int(saiu_mais9.sum()),
        status=status,
        horas=horas,
        donos=donos,
        bases=bases,
    )


def _rows(items: list[dict], keys: tuple[str, ...]) -> str:
    lines = []
    for item in items:
        cells = []
        for key in keys:
            value = item[key]
            text = value if isinstance(value, str) else _pt(int(value))
            cells.append(f"<td>{text}</td>")
        lines.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(lines)


def render_corte_html(report: CorteReport) -> str:
    hoje_delta = report.com["HOJE"] - report.sem["HOJE"]
    fora_delta = report.com["FORA"] - report.sem["FORA"]
    depois_delta = report.com["DEPOIS"] - report.sem["DEPOIS"]
    mais9_delta = report.mais9_com - report.mais9_sem
    status = "".join(f"<tr><td>{nome}</td><td>{_pt(qtd)}</td></tr>" for nome, qtd in report.status)
    horas = "".join(
        f"<tr><td>{row['hora']}</td><td>{_pt(int(row['cedo']))}</td>"
        f"<td>{_pt(int(row['corte']))}</td><td>{_pt(int(row['cedo']) + int(row['corte']))}</td></tr>"
        for row in report.horas
    )
    donos = _rows(report.donos, ("nome", "corte", "hoje", "ontem", "fora", "mais9"))
    bases = _rows(report.bases, ("base", "nome", "corte", "hoje", "ontem", "fora", "mais9"))
    data = report.as_of.strftime("%d/%m/%Y")
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Corte D+1 após 08:00 — {data}</title>
<style>
  body {{ font: 14px/1.45 Segoe UI, sans-serif; margin: 28px; color: #111; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 28px 0 8px; }}
  p {{ margin: 8px 0; max-width: 820px; }}
  .fonte {{ color: #444; font-size: 12px; }}
  .nums {{ display: flex; gap: 18px; flex-wrap: wrap; margin: 16px 0; }}
  .nums div {{ border: 1px solid #ccc; padding: 10px 14px; min-width: 160px; }}
  .nums b {{ display: block; font-size: 26px; }}
  table {{ border-collapse: collapse; margin: 8px 0 4px; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: right; white-space: nowrap; }}
  th {{ background: #111; color: #fff; cursor: pointer; position: sticky; top: 0; }}
  td:first-child, th:first-child, td:nth-child(2), th:nth-child(2) {{ text-align: left; }}
  input {{ padding: 6px 8px; width: 280px; margin: 8px 0; }}
  .nota {{ font-size: 12px; color: #333; }}
</style>
</head>
<body>
<h1>Corte D+1 depois das 08:00</h1>
<p class="fonte">Extração {data} · carteira do prazo · {_pt(report.carteira)} pacotes no último ponto</p>
<p>Cidade com prazo D0 e inbound até 08:00 continua vencendo no mesmo dia. Inbound depois das 08:00 vence no dia seguinte. 08:00 em ponto não muda.</p>
<div class="nums">
  <div><b>{_pt(report.corte)}</b>D0 depois das 08:00</div>
  <div><b>{_pt(report.d0_cedo)}</b>D0 até 08:00, sem mudança</div>
  <div><b>{_pt(report.chegou_hoje)}</b>saíram de vence hoje</div>
  <div><b>{_pt(report.chegou_ontem)}</b>entraram em vence hoje</div>
  <div><b>{_pt(report.saiu_mais9)}</b>saíram da cobrança +9d</div>
</div>
<h2>Carteira inteira, antes e depois da regra</h2>
<table>
  <tr><th>Faixa</th><th>Sem o corte</th><th>Com o corte</th><th>Diferença</th></tr>
  <tr><td>Vence hoje</td><td>{_pt(report.sem['HOJE'])}</td><td>{_pt(report.com['HOJE'])}</td><td>{_pt(hoje_delta)}</td></tr>
  <tr><td>Fora do prazo</td><td>{_pt(report.sem['FORA'])}</td><td>{_pt(report.com['FORA'])}</td><td>{_pt(fora_delta)}</td></tr>
  <tr><td>Vence depois</td><td>{_pt(report.sem['DEPOIS'])}</td><td>{_pt(report.com['DEPOIS'])}</td><td>{_pt(depois_delta)}</td></tr>
  <tr><td>Cobrança +9 dias</td><td>{_pt(report.mais9_sem)}</td><td>{_pt(report.mais9_com)}</td><td>{_pt(mais9_delta)}</td></tr>
</table>
<p class="nota">A diferença de vence hoje é {_pt(report.chegou_ontem)} que entraram menos {_pt(report.chegou_hoje)} que saíram. A cobrança de +9 dias vai de {_pt(report.mais9_sem)} para {_pt(report.mais9_com)}.</p>
<h2>O que aconteceu com os {_pt(report.corte)}</h2>
<table>
  <tr><th>Chegada</th><th>Pacotes</th><th>Sem o corte</th><th>Com o corte</th></tr>
  <tr><td>No dia da extração, depois das 08:00</td><td>{_pt(report.chegou_hoje)}</td><td>Vence hoje</td><td>Vence no dia seguinte</td></tr>
  <tr><td>Véspera, depois das 08:00</td><td>{_pt(report.chegou_ontem)}</td><td>1 dia fora</td><td>Vence hoje</td></tr>
  <tr><td>Dois dias ou antes</td><td>{_pt(report.segue_fora)}</td><td>Fora</td><td>Fora, 1 dia a menos</td></tr>
</table>
<h2>Status desses {_pt(report.corte)}</h2>
<table><tr><th>Status</th><th>Pacotes</th></tr>{status}</table>
<h2>Hora do inbound, só cidade D0</h2>
<p class="nota">"Virou D+1" é o corte. "Ficou D0" chegou naquela hora até 08:00.</p>
<table>
  <tr><th>Hora</th><th>Ficou D0</th><th>Virou D+1</th><th>Total D0</th></tr>
  {horas}
</table>
<h2>Por responsável</h2>
<p class="nota">"Saiu de hoje" chegou no dia da extração depois das 08:00. "Entrou em hoje" chegou na véspera depois das 08:00.</p>
<table>
  <tr><th>Responsável</th><th>No corte</th><th>Saiu de hoje</th><th>Entrou em hoje</th><th>Segue fora</th><th>Saiu do +9</th></tr>
  {donos}
</table>
<h2>Por base</h2>
<input id="q" placeholder="filtrar base ou responsável">
<p class="nota" id="conta"></p>
<table id="bases">
  <tr><th>Base</th><th>Responsável</th><th>No corte</th><th>Saiu de hoje</th><th>Entrou em hoje</th><th>Segue fora</th><th>Saiu do +9</th></tr>
  {bases}
</table>
<script>
function num(text) {{
  var digits = text.replace(/\\./g, '');
  var value = parseInt(digits, 10);
  return digits === String(value) ? value : NaN;
}}
function filtrar() {{
  var q = document.getElementById('q').value.toLowerCase();
  var rows = document.querySelectorAll('#bases tr');
  var shown = 0, total = 0;
  for (var i = 1; i < rows.length; i++) {{
    var ok = rows[i].innerText.toLowerCase().indexOf(q) >= 0;
    rows[i].style.display = ok ? '' : 'none';
    if (ok) {{ shown++; total += num(rows[i].cells[2].innerText) || 0; }}
  }}
  document.getElementById('conta').innerText = shown + ' bases · ' + total.toLocaleString('pt-BR') + ' pacotes no corte';
}}
document.getElementById('q').addEventListener('input', filtrar);
document.querySelectorAll('table').forEach(function(table) {{
  table.querySelectorAll('th').forEach(function(th, idx) {{
    th.addEventListener('click', function() {{
      var body = Array.prototype.slice.call(table.rows, 1);
      var dir = th.dataset.dir === '1' ? -1 : 1;
      th.dataset.dir = String(dir);
      body.sort(function(a, b) {{
        var x = a.cells[idx].innerText, y = b.cells[idx].innerText;
        var nx = num(x), ny = num(y);
        if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * dir;
        return x.localeCompare(y, 'pt') * dir;
      }});
      body.forEach(function(row) {{ table.appendChild(row); }});
    }});
  }});
}});
filtrar();
</script>
</body>
</html>
"""
