import os
import re
from contextlib import contextmanager
from urllib.parse import urlparse

from flask import current_app
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

INDICIOS_EMPRESA = ("minhas vagas", "recrutamento", "área da empresa", "area da empresa", "empresa")
INDICIOS_CANDIDATO = ("inscrito", "candidato", "currículo", "curriculo", "cv")


def caminho_cookie():
    return os.path.join(os.path.dirname(current_app.config["DATABASE"]), "infojobs_cookie.txt")


def salvar_cookie(valor):
    with open(caminho_cookie(), "w", encoding="utf-8") as arquivo:
        arquivo.write((valor or "").strip())


def remover_cookie():
    if os.path.exists(caminho_cookie()):
        os.remove(caminho_cookie())


def ler_cookie():
    try:
        with open(caminho_cookie(), "r", encoding="utf-8") as arquivo:
            return arquivo.read().strip()
    except FileNotFoundError:
        return ""


def tem_cookie():
    return bool(ler_cookie())


def _cookies():
    valor = ler_cookie()
    if not valor:
        return []
    valor = re.sub(r"^cookie\s*:", "", valor, flags=re.I).strip()
    cookies = []
    for parte in valor.split(";"):
        if "=" not in parte:
            continue
        nome, conteudo = parte.split("=", 1)
        nome = nome.strip()
        if not nome:
            continue
        cookies.append(
            {
                "name": nome,
                "value": conteudo.strip(),
                "domain": ".infojobs.com.br",
                "path": "/",
            }
        )
    return cookies


@contextmanager
def sessao():
    with sync_playwright() as motor:
        navegador = motor.chromium.connect_over_cdp(current_app.config["WHATSAPP_CDP_URL"])
        contexto = navegador.contexts[0]
        cookies = _cookies()
        if cookies:
            contexto.add_cookies(cookies)
        yield contexto


def _absoluto(href, base):
    if not href:
        return ""
    if href.startswith("http"):
        return href
    origem = urlparse(base)
    if href.startswith("/"):
        return f"{origem.scheme}://{origem.netloc}{href}"
    return f"{base.rstrip('/')}/{href.lstrip('/')}"


def _links(pagina):
    return pagina.locator("a[href]").evaluate_all(
        """els => els.map(e => ({
            texto: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 120),
            href: e.href || ''
        }))"""
    )


def inspecionar():
    if not tem_cookie():
        return {"erro": "Cookie da Área Empresa não configurado.", "links": []}
    with sessao() as contexto:
        pagina = contexto.new_page()
        pagina.set_default_timeout(10000)
        pagina.goto("https://www.infojobs.com.br/", wait_until="domcontentloaded", timeout=30000)
        pagina.wait_for_timeout(4000)
        links = _links(pagina)
        corpo = pagina.locator("body").inner_text()[:1200]
        relevantes = []
        for item in links:
            alvo = f"{item['texto']} {item['href']}".lower()
            if any(pista in alvo for pista in INDICIOS_EMPRESA + INDICIOS_CANDIDATO):
                item["href"] = _absoluto(item["href"], pagina.url)
                relevantes.append(item)
        pagina.close()
        return {"url": "https://www.infojobs.com.br/", "corpo": corpo, "links": relevantes[:30]}


def descobrir_vagas():
    if not tem_cookie():
        return {"erro": "Cookie da Área Empresa não configurado.", "vagas": []}
    with sessao() as contexto:
        pagina = contexto.new_page()
        pagina.set_default_timeout(10000)
        pagina.goto("https://www.infojobs.com.br/", wait_until="domcontentloaded", timeout=30000)
        pagina.wait_for_timeout(4000)
        links = _links(pagina)
        area = ""
        for item in links:
            alvo = f"{item['texto']} {item['href']}".lower()
            if "minhas vagas" in alvo or "recrutamento" in alvo:
                area = _absoluto(item["href"], pagina.url)
                break
        if area:
            try:
                pagina.goto(area, wait_until="domcontentloaded", timeout=30000)
                pagina.wait_for_timeout(4000)
            except PlaywrightTimeout:
                pass
        vagas = []
        vistos = set()
        for item in _links(pagina):
            alvo = f"{item['texto']} {item['href']}".lower()
            href = _absoluto(item["href"], pagina.url)
            if not href or href in vistos:
                continue
            if "infojobs.com.br" not in urlparse(href).netloc:
                continue
            if "vaga" not in alvo and "inscrito" not in alvo:
                continue
            if any(pista in alvo for pista in ("minhas vagas", "empregos.aspx", "buscar")):
                continue
            vistos.add(href)
            vagas.append({"titulo": item["texto"] or "Vaga", "url": href})
        pagina.close()
        return {"area": area, "vagas": vagas[:40]}


def buscar_inscritos(url_vaga, limite=30):
    if not tem_cookie():
        raise RuntimeError("Cookie da Área Empresa não configurado.")
    with sessao() as contexto:
        pagina = contexto.new_page()
        pagina.set_default_timeout(10000)
        pagina.goto(url_vaga, wait_until="domcontentloaded", timeout=30000)
        pagina.wait_for_timeout(5000)
        aba = pagina.locator("text=Inscritos")
        if aba.count():
            try:
                aba.first.click(timeout=5000)
                pagina.wait_for_timeout(3500)
            except PlaywrightTimeout:
                pass
        seletores = (
            '[data-testid*="candidate"], [data-testid*="inscrito"], '
            '[data-testid*="applicant"], .candidate, .inscrito, tr, li'
        )
        cartoes = pagina.locator(seletores)
        resultados = []
        for indice in range(min(cartoes.count(), limite * 3)):
            texto = re.sub(r"\s+", " ", cartoes.nth(indice).inner_text()).strip()
            if not texto or len(texto) < 8:
                continue
            if not any(pista in texto.lower() for pista in INDICIOS_CANDIDATO):
                continue
            nome = ""
            for seletor in ("h2", "h3", "strong", "a", ".nome", "[class*=name]"):
                local = cartoes.nth(indice).locator(seletor).first
                if local.count():
                    nome = re.sub(r"\s+", " ", local.inner_text()).strip()
                    if nome:
                        break
            if not nome:
                nome = texto.split(" - ")[0].split("  ")[0][:80].strip()
            telefone = re.search(r"(?:\+55\s?)?\(?\d{2}\)?[\s.-]*9?\d{4}[\s.-]*\d{4}", texto)
            cidade = ""
            partes = [p.strip() for p in texto.split(" - ") if p.strip()]
            if len(partes) > 1:
                cidade = partes[1][:60]
            link = ""
            ancora = cartoes.nth(indice).locator("a[href]").first
            if ancora.count():
                link = _absoluto(ancora.get_attribute("href"), pagina.url)
            if nome and not any(r["nome"] == nome for r in resultados):
                resultados.append(
                    {
                        "nome": nome[:120],
                        "telefone": telefone.group(0) if telefone else "",
                        "cidade": cidade,
                        "link": link,
                        "resumo": texto[:240],
                    }
                )
            if len(resultados) >= limite:
                break
        pagina.close()
        return resultados
