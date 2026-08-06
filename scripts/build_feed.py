#!/usr/bin/env python3
"""
Gera/atualiza o carteiras.json a partir das matérias consolidadas de "carteiras
recomendadas" do InfoMoney (HTML estático com tabela ticker x nº de recomendações).

Roda 1x/dia no GitHub Actions: descobre a matéria mais recente de cada categoria,
raspa a tabela, e grava a composição do mês na história de cada cesta. Meses antigos
são preservados — é isso que alimenta a movimentação e o histórico de acerto no app.

Sem dependência de login/paywall. Se uma categoria falhar, as demais seguem e o mês
anterior é mantido (nunca apaga histórico).
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
FEED_PATH = ROOT / "carteiras.json"
INDEX_URL = "https://www.infomoney.com.br/tudo-sobre/carteira-recomendada/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Ticker da B3: 4 letras + 1 ou 2 dígitos (ITUB4, TAEE11, ALOS3, MXRF11).
TICKER_RE = re.compile(r"\b[A-Z]{4}\d{1,2}\b")

# Cabeçalho da coluna de contagem. NÃO casa "carteira" no singular de propósito:
# as tabelas trazem uma linha de título tipo "Carteira Small Caps" antes do cabeçalho,
# e casar com ela apontava a contagem para a coluna de nome da empresa.
COUNT_HEADER_RE = re.compile(r"recomenda|indica|n[º°o]\s*de\s*carteiras|carteiras")

MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}

# Como classificar o slug de cada matéria em uma categoria do app.
# Ordem importa: os testes mais específicos vêm primeiro.
def classify(slug: str) -> str | None:
    s = slug.lower()
    if any(x in s for x in ("bdr", "gringa", "internacion", "cripto")):
        return None
    if "small-cap" in s or "small-caps" in s:
        return "SMALL_CAP"
    if "fundos-imobiliarios" in s or "fiis" in s or "-fii" in s:
        return "FII"
    if "dividendos" in s or "dividendo" in s:
        return "DIVIDENDOS"
    if "acoes-mais-recomendadas" in s or "acoes-mais-indicadas" in s \
            or "acoes-recomendadas" in s or "mais-indicadas-para-investir" in s:
        return "CRESCIMENTO"
    return None


def month_from_slug(slug: str) -> str | None:
    """Extrai 'yyyy-MM' do slug quando ele traz o mês por extenso (ex.: '...-julho-2026')."""
    s = slug.lower()
    year = None
    ym = re.search(r"20\d{2}", s)
    if ym:
        year = int(ym.group(0))
    for nome, num in MESES.items():
        if nome in s:
            y = year or datetime.now(timezone.utc).year
            return f"{y:04d}-{num:02d}"
    return None


def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    # Sem charset no header, o requests assume ISO-8859-1 e "Indicações" chega corrompido.
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def looks_like_count(values: list[str]) -> bool:
    """A coluna passa como 'nº de recomendações' se for majoritariamente inteiro de 1 a 20."""
    nums = [int(v) for v in values if re.fullmatch(r"\d{1,2}", v.strip())]
    return len(nums) >= 3 and all(1 <= n <= 20 for n in nums)


def discover_articles() -> dict[str, str]:
    """Retorna {categoria: url} com a matéria mais recente de cada categoria no índice.

    Coleta todos os candidatos por categoria e escolhe o de mês mais recente (pelo slug);
    sem mês no slug, cai na ordem do índice (que já vem do mais novo p/ o mais antigo).
    """
    html = fetch(INDEX_URL)
    soup = BeautifulSoup(html, "lxml")
    cands: dict[str, list[tuple[str, str]]] = {}   # cat -> [(url, "yyyy-MM" ou "")]
    for a in soup.select("a[href]"):
        href = a["href"]
        if "/onde-investir/" not in href:
            continue
        url = href if href.startswith("http") else "https://www.infomoney.com.br" + href
        slug = url.rstrip("/").split("/")[-1]
        cat = classify(slug)
        if not cat:
            continue
        lst = cands.setdefault(cat, [])
        if url not in {u for u, _ in lst}:
            lst.append((url, month_from_slug(slug) or ""))

    found: dict[str, str] = {}
    for cat, lst in cands.items():
        dated = [x for x in lst if x[1]]
        found[cat] = max(dated, key=lambda x: x[1])[0] if dated else lst[0][0]
    return found


def parse_holdings(url: str) -> list[dict]:
    """Lê a melhor tabela da matéria e devolve [{'t': ticker, 'c': nº recomendações}]."""
    html = fetch(url)
    soup = BeautifulSoup(html, "lxml")
    best: list[dict] = []

    for table in soup.find_all("table"):
        matrix: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                matrix.append(cells)
        if len(matrix) < 3:
            continue
        ncol = max(len(r) for r in matrix)

        def column(j: int) -> list[str]:
            return [(r[j] if j < len(r) else "") for r in matrix]

        # Coluna de ticker = a que mais casa com o padrão de ticker.
        ticker_col, ticker_hits = None, 0
        for j in range(ncol):
            hits = sum(1 for v in column(j) if TICKER_RE.search(v.upper()))
            if hits > ticker_hits:
                ticker_col, ticker_hits = j, hits
        if ticker_col is None or ticker_hits < 3:
            continue

        # Linha de cabeçalho: a 1ª das 3 primeiras com ≥2 células preenchidas e sem ticker.
        # A matéria costuma abrir com uma linha de título mesclada ("Carteira Small Caps"),
        # que não é cabeçalho — tratá-la como tal apontava a contagem para a coluna errada.
        header_row = 0
        for i, r in enumerate(matrix[:3]):
            if len([c for c in r if c.strip()]) >= 2 \
                    and not any(TICKER_RE.search(c.upper()) for c in r):
                header_row = i
                break

        # Coluna de contagem: header com "recomenda/indica/carteiras", senão coluna numérica.
        header = [c.lower() for c in matrix[header_row]]
        count_col = None
        for j, h in enumerate(header):
            if COUNT_HEADER_RE.search(h):
                count_col = j
                break
        # O header pode apontar errado: só aceita a coluna se ela for de fato numérica.
        if count_col is None or not looks_like_count(column(count_col)[header_row + 1:]):
            count_col = None
            for j in range(ncol):
                if j == ticker_col:
                    continue
                if looks_like_count(column(j)[header_row + 1:]):
                    count_col = j
                    break
        # Sem contagem confiável a tabela é descartada: melhor manter o mês anterior do que
        # gravar um mês inteiro com c=0 e estragar o ranking de convicção no app.
        if count_col is None:
            continue

        holdings, seen = [], set()
        for r in matrix:
            joined = " ".join(r).upper()
            m = TICKER_RE.search(r[ticker_col].upper() if ticker_col < len(r) else "") \
                or TICKER_RE.search(joined)
            if not m:
                continue
            t = m.group(0)
            if t in seen:
                continue
            seen.add(t)
            c = 0
            if count_col is not None and count_col < len(r):
                cm = re.search(r"\d+", r[count_col])
                if cm:
                    c = int(cm.group(0))
            holdings.append({"t": t, "c": c})
        if len(holdings) > len(best):
            best = holdings
    return best


def load_feed() -> dict:
    if FEED_PATH.exists():
        try:
            return json.loads(FEED_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"asOf": "", "updatedAt": 0, "categories": []}


def main() -> int:
    feed = load_feed()
    cats_by_id = {c["category"]: c for c in feed.get("categories", [])}

    try:
        articles = discover_articles()
    except Exception as e:
        print(f"ERRO ao descobrir matérias: {e}", file=sys.stderr)
        return 1

    if not articles:
        print("Nenhuma matéria encontrada no índice — abortando sem alterar o feed.")
        return 0

    now = datetime.now(timezone.utc)
    default_month = f"{now.year:04d}-{now.month:02d}"
    changed = False

    for cat, url in articles.items():
        slug = url.rstrip("/").split("/")[-1]
        month = month_from_slug(slug) or default_month
        try:
            holdings = parse_holdings(url)
        except Exception as e:
            print(f"[{cat}] falha ao raspar {url}: {e}", file=sys.stderr)
            continue
        if not holdings:
            print(f"[{cat}] sem tickers extraídos de {url}")
            continue

        entry = cats_by_id.get(cat)
        if entry is None:
            entry = {"category": cat, "source": "InfoMoney (consolidado)", "history": {}}
            cats_by_id[cat] = entry
        prev = entry["history"].get(month)
        if prev != holdings:
            entry["history"][month] = holdings
            changed = True
        print(f"[{cat}] {month}: {len(holdings)} ativos ({url})")
        time.sleep(1)  # gentileza com o servidor

    feed["categories"] = list(cats_by_id.values())
    feed["asOf"] = max((m for c in feed["categories"] for m in c["history"]), default=default_month)
    if changed:
        feed["updatedAt"] = int(now.timestamp() * 1000)

    FEED_PATH.write_text(
        json.dumps(feed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Feed atualizado." if changed else "Sem mudanças no feed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
