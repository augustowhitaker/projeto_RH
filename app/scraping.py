import json
import os
import re
import time
import urllib.request
from urllib.parse import urlencode, urlparse

from flask import current_app
from playwright.sync_api import sync_playwright

CIDADES_PROXIMAS = {
    "sao paulo": ["Campinas", "Sumaré", "Americana", "Guarulhos", "Santo André"],
    "campinas": ["Sumaré", "Americana", "Hortolândia", "Paulínia", "Atibaia"],
    "rio de janeiro": ["Niterói", "São Gonçalo", "Duque de Caxias", "Nova Iguaçu"],
    "belo horizonte": ["Contagem", "Betim", "Sabará", "Nova Lima"],
    "curitiba": ["São José dos Pinhais", "Araucária", "Campo Largo"],
    "porto alegre": ["Canoas", "São Leopoldo", "Novo Hamburgo"],
}


def cidades_proximas(cidade):
    chave = (cidade or "").strip().lower()
    for nome, proximas in CIDADES_PROXIMAS.items():
        if nome in chave:
            return proximas
    return []


def resolver_localizacao(cidade, estado=""):
    nome = (cidade or "").strip()
    uf = (estado or "").strip().upper()
    if not nome:
        return None
    consulta = f"{nome} {uf}" if uf and uf not in nome.upper() else nome
    url = (
        "https://www.infojobs.com.br/mf-publicarea/api/autocompleteapi/locations?"
        + urlencode({"query": consulta})
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resposta:
            dados = json.load(resposta)
    except Exception:
        return None
    sugestoes = dados.get("suggestions", [])
    alvo = f"{nome} - {uf}".upper() if uf else nome.upper()
    for sugestao in sugestoes:
        if str(sugestao.get("value", "")).upper() == alvo:
            return str(sugestao.get("data", {}).get("id", "")) or None
    for sugestao in sugestoes:
        if str(sugestao.get("value", "")).upper().startswith(nome.upper()):
            return str(sugestao.get("data", {}).get("id", "")) or None
    return None


def _texto_limpo(valor):
    return re.sub(r"\s+", " ", valor or "").strip()


def buscar(url, termo="", cidades=None, limite=30):
    cidades = [c for c in (cidades or []) if c]
    seletor_cartao = os.getenv(
        "SCRAP_SELECTOR_CARD",
        ".js_rowCard, article, li[data-testid], .resultado, .card",
    )
    seletor_nome = os.getenv(
        "SCRAP_SELECTOR_NOME",
        ".js_vacancyTitle, h2, h3, .titulo, a",
    )
    seletor_link = os.getenv("SCRAP_SELECTOR_LINK", 'a[href*="/vaga-"], a[href]')

    resultados = []
    with sync_playwright() as motor:
        navegador = motor.chromium.connect_over_cdp(
            current_app.config["WHATSAPP_CDP_URL"]
        )
        contexto = navegador.contexts[0]
        pagina = contexto.new_page()
        pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
        pagina.wait_for_timeout(4000)
        cartoes = pagina.locator(seletor_cartao)
        total = min(cartoes.count(), limite)
        for indice in range(total):
            cartao = cartoes.nth(indice)
            titulo = cartao.locator(seletor_nome).first
            link = cartao.locator(seletor_link).first
            nome = _texto_limpo(titulo.inner_text()) if titulo.count() else ""
            if not nome:
                nome = _texto_limpo(cartao.inner_text())[:80]
            href = link.get_attribute("href") if link.count() else None
            if href and href.startswith("/"):
                origem = urlparse(url)
                href = f"{origem.scheme}://{origem.netloc}{href}"
            texto = _texto_limpo(cartao.inner_text())
            cidade = next((c for c in cidades if c.lower() in texto.lower()), "")
            if cidades and not cidade:
                continue
            if termo and termo.lower() not in texto.lower():
                continue
            if not nome:
                continue
            resultados.append(
                {"nome": nome[:120], "cidade": cidade, "link": href, "resumo": texto[:240]}
            )
        pagina.close()
    return resultados


def baixar_curriculo(url, caminho):
    with sync_playwright() as motor:
        navegador = motor.chromium.connect_over_cdp(
            current_app.config["WHATSAPP_CDP_URL"]
        )
        resposta = navegador.contexts[0].request.get(url, timeout=60000)
        if not resposta.ok:
            raise RuntimeError(f"Falha ao baixar: HTTP {resposta.status}")
        with open(caminho, "wb") as arquivo:
            arquivo.write(resposta.body())
    return caminho
