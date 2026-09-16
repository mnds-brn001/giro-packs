# Rotina

## Pack do dia

Coloque o export em `data/monitoramento/` e o prazo em `data/prazo/`:

```powershell
python scripts\generate_daily_pack.py --as-of YYYY-MM-DD
```

O ingest grava `data/parquet/monitoramento_YYYY-MM-DD.parquet` (e `monitoramento_latest.parquet`) para não reconverter o xlsx no mesmo dia.

Saída em `output/lastmile_YYYY-MM-DD/`:

- `INDEX.txt` — volume por responsável
- `<Responsavel>/mensagens_whatsapp/` — backlog (+10d) com bloco `AJs +10d para copiar`
- `<Responsavel>/mensagens_preventivo/` — vence hoje (inbound + D0/D+N)
- `<Responsavel>/malha_acao_*.xlsx` — recorte da carteira
- `malha_acao/painel_malha_*.xlsx` — painel completo
- `tiktok_sla/mensagens_whatsapp/` — FORA / VENCE HOJE + `AJs TikTok para copiar`

## CLIs avulsos

Os geradores soltos leem Excel/Parquet e gravam `output/<rotina>_<data>/`:

- `*.xlsx` — resumo por base + detalhe / críticos
- `mensagens_whatsapp\BASE.txt` — copy por unidade
- `TODAS_AS_BASES.txt` — concatenado para revisão

O preventivo ainda aceita `--hq` (RESUMO da matriz) ou `--prazo` (inbound + TIME DELIVERY DIAS). Marketplace `due_at` sozinho subestima “vence hoje”.

O retorno ao hub lê a aba `DADOS` do acompanhamento, parseia origem/hub no rastreio (`saiu de [BASE] … centro de trânsito de [HUB]`) e classifica 0–3 / 4–6 / 7–9 / ≥10.

A malha cruza ponto pré-alocado × ponto físico. Ação:

- **Devolver HUB** — no piso, base errada
- **Não entregar — devolver HUB** — em rota na base errada
- **Encaminhar ao pré-alocado** — parado no hub / SP-RR
