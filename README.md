# giro-packs

CLI para transformar export de tracking (planilha mestra bilingue) em **pacotes de giro** por base:

| Rotina | O que sai |
|---|---|
| **Backlog** | Fila +10 dias: piso por cidade, rota por entregador. Copy de tratativa. |
| **Preventivo** | Vence hoje (inbound + prazo D0/D+N). Copy de encerramento de turno. |
| **Retorno ao hub** | Aging 1–3 / 4–6 / 7–9 / +10. Copy de cobrança física (manifesto vs piso). |

Não envia WhatsApp. Gera Excel + `.txt` por base para revisão humana.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```powershell
# Backlog (tratativa)
python scripts\generate_lastmile_backlog.py `
  --input "C:\path\to\tracking.xlsx" `
  --as-of 2026-08-26 `
  --out output\backlog_2026-08-26

# Preventivo (vence hoje)
python scripts\generate_lastmile_preventivo.py `
  --input "C:\path\to\tracking.xlsx" `
  --as-of 2026-08-26 `
  --prazo "C:\path\to\prazos.xlsx" `
  --out output\preventivo_2026-08-26

# Devolução ao hub superior
python scripts\generate_hub_return.py `
  --input "C:\path\to\hub-return.xlsx" `
  --as-of 2026-08-26 `
  --out output\hub_return_2026-08-26
```

`--responsavel` filtra a carteira quando o arquivo de prazo/responsabilidade está disponível.

## Testes

```bash
python -m pytest -q
```

## Contrato

- Fonte: aba de dados da extração (colunas canônicas em `src/columns.py`).
- Aging e RESUMO são regras determinísticas no código — não Copilot.
- Sem evidência de manifesto, tratar retorno ao hub como não enviado.
