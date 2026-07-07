# carteiras-feed

Feed de **carteiras recomendadas consolidadas** (consenso de corretoras) que alimenta a aba
"Carteiras" do app de Finanças. Um GitHub Action roda 1x/dia, raspa as matérias consolidadas
do InfoMoney e atualiza o `carteiras.json`. O app lê esse arquivo pela URL "raw".

## Como funciona

- `scripts/build_feed.py` — descobre a matéria mais recente de cada categoria (Dividendos,
  Crescimento, FIIs, Small Caps) no índice do InfoMoney, raspa a tabela `ticker × nº de
  recomendações` e grava a composição do mês em `carteiras.json`. **Meses antigos são
  preservados** — é isso que gera a movimentação e o histórico de acerto no app.
- `.github/workflows/update.yml` — agenda o script (cron diário) e comita o resultado.
- `carteiras.json` — o feed servido ao app (começa com dados de exemplo, substituídos na 1ª execução).

## Passo a passo para colocar no ar

1. **Crie um repositório** no GitHub (ex.: `carteiras-feed`). Deixe **público** — assim o
   GitHub Actions é grátis e ilimitado. (Privado também funciona, dentro da cota mensal gratuita.)
2. **Suba estes arquivos** para a raiz do repositório (`carteiras.json`, `scripts/`, `.github/`).
3. Em **Settings → Actions → General → Workflow permissions**, marque
   **"Read and write permissions"** (deixa o bot comitar o JSON de volta).
4. Vá em **Actions**, abra o workflow "Atualiza carteiras" e clique em **"Run workflow"** para
   testar agora (sem esperar o cron). Veja os logs: deve listar quantos ativos raspou por categoria.
5. Pegue a **URL raw** do `carteiras.json`: abra o arquivo no GitHub → botão **"Raw"** → copie a URL.
   Fica assim: `https://raw.githubusercontent.com/SEU_USUARIO/carteiras-feed/main/carteiras.json`
6. No app, em `AnalystPortfolioSource.kt`, troque a constante `FEED_URL` por essa URL. Pronto.

## Rodar localmente (opcional, para testar o raspador)

```bash
pip install requests beautifulsoup4 lxml
python scripts/build_feed.py
```

## Se o raspador quebrar

O InfoMoney muda o HTML de vez em quando. Sintomas nos logs do Action:
- "Nenhuma matéria encontrada" → o índice mudou de estrutura (ajustar `discover_articles`).
- "[CATEGORIA] sem tickers extraídos" → a tabela da matéria mudou (ajustar `parse_holdings`).

Nesses casos o feed **não é apagado** — ele mantém o último mês bom até o raspador ser corrigido.

## Notas

- Os dados são o **consenso** (nº de corretoras que recomendam cada ativo), não os pesos %
  de cada casa — que são pagos/fechados. O app trata a cesta como equal-weight no histórico de acerto.
- Fonte: matérias públicas do InfoMoney, que consolidam ~10 corretoras (Itaú BBA, BTG, XP,
  Santander, Genial, Terra, BB etc.). Uso pessoal; cite a fonte se for redistribuir.
