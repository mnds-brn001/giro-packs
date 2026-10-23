# Corte D+1 depois das 08:00

Cidade com `TIME DELIVERY DIAS` = 0 vence no dia do inbound. Se o inbound no ponto passou das 08:00, o vencimento vai para o dia seguinte. 08:00 em ponto continua no mesmo dia. D+1, D+2 e D+3 não mudam.

A regra já vale no preventivo e no TikTok, porque os dois chamam `apply_inbound_resumo` em `src/preventivo_lastmile.py`. A coluna `corte_8h` na planilha do preventivo fica `D+1` quando a regra disparou. `lm_dias` continua o prazo da cidade. `lm_vencimento` é o prazo efetivo.

O HTML que conta o efeito **não** sai com o pack. É comando à parte, para ligar depois.

```powershell
python scripts\generate_corte_8h.py --as-of YYYY-MM-DD
```

Grava `output/lastmile_YYYY-MM-DD/corte_8h.html`. Sem `--input`, usa o parquet do dia se ele já existir (`data/parquet/monitoramento_YYYY-MM-DD.parquet`). Prazo em `data/prazo/`.

## O que o HTML compara

População: pacote cujo último ponto está na carteira do prazo. Não filtra região de novo e não tira status.

O "sem o corte" recalcula o vencimento com o `lm_dias` da cidade, sem somar o dia. O "com o corte" é o `resumo` que o preventivo já gravou.

Dentro dos pacotes marcados `D+1`:

| Chegada | Sem o corte | Com o corte |
|---|---|---|
| No dia da extração, depois das 08:00 | Vence hoje | Vence no dia seguinte |
| Véspera, depois das 08:00 | 1 dia fora | Vence hoje |
| Dois dias ou antes | Fora | Fora, 1 dia a menos |

A cobrança de +9 dias é `dias_fora >= 9` (`FORA_MIN_DAYS`). Quem estava justo em 9 dias e ganhou um dia de prazo sai dessa cobrança. Quem já estava além continua fora.

Na extração de 22/10/2026 22:00 a carteira tinha 81.545 pacotes. 34.638 eram D0 depois das 08:00. 21.270 eram D0 até 08:00 e não mudaram. Vence hoje foi de 36.473 para 23.055. A cobrança de +9 dias foi de 3.071 para 2.872.

## Onde ligar no pack

O encaixe é `scripts/generate_lastmile_by_owner.py`, depois do loop dos responsáveis e antes de gravar o `INDEX.txt`. Nesse ponto já existem `tracking`, `prazo_lookup`, `responsabilidade`, `reference` e `out_root`.

```python
from src.corte_8h import build_corte_report, render_corte_html

report = build_corte_report(tracking, prazo_lookup, responsabilidade, reference)
(out_root / "corte_8h.html").write_text(render_corte_html(report), encoding="utf-8")
```

`generate_daily_pack.py` só dispara os outros scripts. Não é o lugar do HTML.

`corte_8h.eq("D+1")` volta como booleano com nulo, porque a coluna tem vazio. Combinar isso com outro booleano pelo `&` zera a conta. O módulo converte com `fillna(False).astype(bool)` antes de cruzar as máscaras.
