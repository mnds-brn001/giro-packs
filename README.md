# giro-packs

CLI para transformar o export de tracking (planilha mestra bilingue) em **pacotes de giro**:

| Rotina | O que sai |
|---|---|
| **Pack do dia** | Backlog + preventivo + malha por responsável + TikTok. Um comando. |
| **Backlog** | Fila +10 dias: piso por cidade, rota por entregador. Copy de tratativa + AJs para copiar. |
| **Preventivo** | Vence hoje (inbound + prazo D0/D+N). Copy de encerramento de turno. |
| **Malha** | Desvio pré-alocado × ponto físico: Devolver HUB / Não entregar / Encaminhar. |
| **TikTok** | Cobrança cirúrgica (inbound + D0/D+N) com bloco de AJs para copiar. |
| **Retorno ao hub** | Aging 1–3 / 4–6 / 7–9 / +10. Copy de cobrança física (manifesto vs piso). |

Não envia WhatsApp. Gera Excel + `.txt` por base para revisão humana.

## Pack do dia (PC de viagem)

```powershell
git clone https://github.com/mnds-brn001/giro-packs.git
cd giro-packs
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

1. Extração Express/SRM (`monitoramento da pontualidade de pedido-20YYMMDD….xlsx`) em `data\monitoramento\`
2. Planilha `prazo e responsabilidade last mile.xlsx` em `data\prazo\`
3. Rodar:

```powershell
python scripts\generate_daily_pack.py --as-of 2026-09-16
```

Se `--as-of` for omitido, a data sai do stamp `20YYMMDD` do filename. Saída em `output\lastmile_YYYY-MM-DD\` (INDEX, pasta por responsável, `malha_acao\`, `tiktok_sla\`).

Não commitar xlsx/parquet/output. O repo é público.

## CLIs avulsos

```powershell
# Pack por responsável (sem TikTok)
python scripts\generate_lastmile_by_owner.py --as-of 2026-09-16 --out output\lastmile_2026-09-16

# TikTok (depois do ingest, usa o parquet)
python scripts\generate_tiktok_sla.py --as-of 2026-09-16 --out output\lastmile_2026-09-16\tiktok_sla --skip-ingest

# Só malha
python scripts\generate_mesh_action.py --as-of 2026-09-16 --out output\lastmile_2026-09-16

# Devolução ao hub superior
python scripts\generate_hub_return.py `
  --input "C:\path\to\hub-return.xlsx" `
  --as-of 2026-09-16 `
  --out output\hub_return_2026-09-16
```

`--responsavel` filtra a carteira quando o arquivo de prazo está em `data\prazo\` (ou via `--prazo`).

## Testes

```bash
python -m pytest -q
```

## Contrato

- Fonte: aba de dados da extração (colunas canônicas em `src/columns.py`).
- Aging e RESUMO são regras determinísticas no código — não Copilot.
- Preventivo e TikTok usam inbound + `TIME DELIVERY DIAS` (D0/D+N), não só `due_at` do marketplace.
- Sem evidência de manifesto, tratar retorno ao hub como não enviado.
- Malha é desvio operacional (reencaminhar / devolver), não correção de CEP.
