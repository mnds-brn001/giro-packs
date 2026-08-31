# Rotina

Os três CLIs leem Excel e gravam `output/<rotina>_<data>/`:

- `*.xlsx` — resumo por base + detalhe / críticos
- `mensagens_whatsapp\BASE.txt` — copy por unidade
- `TODAS_AS_BASES.txt` — concatenado para revisão

O preventivo ainda aceita `--hq` (RESUMO da matriz) ou `--prazo` (inbound + TIME DELIVERY DIAS). Marketplace `due_at` sozinho subestima “vence hoje”.

O retorno ao hub lê a aba `DADOS` do acompanhamento, parseia origem/hub no rastreio (`saiu de [BASE] … centro de trânsito de [HUB]`) e classifica 0–3 / 4–6 / 7–9 / ≥10.
