import io
import re
import time
from contextlib import contextmanager
from threading import Lock

from flask import current_app
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

_trava = Lock()


@contextmanager
def _pagina():
    with _trava:
        with sync_playwright() as motor:
            navegador = motor.chromium.connect_over_cdp(
                current_app.config["WHATSAPP_CDP_URL"]
            )
            contexto = navegador.contexts[0]
            paginas = [p for p in contexto.pages if "web.whatsapp.com" in p.url]
            pagina = paginas[0] if paginas else contexto.new_page()
            pagina.set_default_timeout(8000)
            sessao_cdp = contexto.new_cdp_session(pagina)
            sessao_cdp.send(
                "Emulation.setUserAgentOverride",
                {"userAgent": current_app.config["WHATSAPP_USER_AGENT"]},
            )
            if "web.whatsapp.com" not in pagina.url:
                try:
                    pagina.goto(
                        "https://web.whatsapp.com/",
                        wait_until="domcontentloaded",
                        timeout=8000,
                    )
                except PlaywrightTimeout:
                    pass
            yield pagina


def _esta_logado(pagina):
    texto = pagina.locator("body").inner_text()
    if any(
        trecho in texto
        for trecho in (
            "Escaneie",
            "Conectar com n",
            "Scan to log in",
            "Link with phone number",
            "update Chrome",
        )
    ):
        return False
    return (
        pagina.locator('[aria-label="Nova conversa"], [aria-label="New chat"]').count() > 0
    )


def status():
    with _pagina() as pagina:
        pagina.wait_for_timeout(1500)
        return {"logado": _esta_logado(pagina)}


def qr_code():
    with _pagina() as pagina:
        pagina.wait_for_timeout(1500)
        if _esta_logado(pagina):
            return None
        elemento = pagina.locator('[data-testid="link-device-qr-code"]')
        elemento.wait_for(state="visible", timeout=15000)
        return io.BytesIO(elemento.screenshot())


def desconectar():
    with _pagina() as pagina:
        sessao = pagina.context.new_cdp_session(pagina)
        sessao.send(
            "Storage.clearDataForOrigin",
            {
                "origin": "https://web.whatsapp.com",
                "storageTypes": "cookies,local_storage,indexeddb,websql,service_workers,cache_storage",
            },
        )
        pagina.goto(
            "https://web.whatsapp.com/",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        pagina.wait_for_timeout(8000)
        return not _esta_logado(pagina)


def _fechar_avisos(pagina, registros):
    while True:
        avisos = pagina.locator('[role="dialog"][aria-label^="Não foi possível adicionar"]')
        if avisos.count() == 0:
            break
        registros.append(avisos.first.get_attribute("aria-label"))
        pagina.keyboard.press("Escape")
        pagina.wait_for_timeout(400)


def criar_grupo(nome, numeros, mensagem=""):
    registros = []
    with _pagina() as pagina:
        if not _esta_logado(pagina):
            raise RuntimeError("WhatsApp Web nao autenticado. Escaneie o QR code.")

        pagina.locator('[aria-label="Nova conversa"]').click()
        pagina.wait_for_timeout(900)
        pagina.locator('[aria-label="Novo grupo"]').click()
        pagina.wait_for_timeout(1300)

        campo = pagina.locator(
            '[data-testid="group_participants_search"] input[data-testid="inputarea"]'
        )
        selecionados = []
        for numero in numeros:
            campo.fill(numero)
            pagina.wait_for_timeout(1100)
            linha = pagina.locator(
                '[data-testid="new-group-drawer-participants"] '
                '[data-testid="cell-frame-container"][role="button"]'
            ).first
            try:
                linha.wait_for(state="visible", timeout=5000)
                linha.click()
                selecionados.append(numero)
                registros.append(f"Adicionado: +55 {numero}")
            except PlaywrightTimeout:
                registros.append(f"Nao encontrado no WhatsApp: +55 {numero}")
            pagina.wait_for_timeout(900)

        if not selecionados:
            raise RuntimeError("Nenhum numero foi reconhecido pelo WhatsApp.")

        pagina.get_by_role("button", name="Avançar").click()
        pagina.wait_for_timeout(1300)
        pagina.locator('[data-testid="group-subject"] [data-testid="text-input"]').fill(nome)
        pagina.wait_for_timeout(400)
        pagina.locator('[data-testid="create-group-btn"]').click()
        pagina.wait_for_timeout(3000)

        confirmacao = pagina.locator('[role="dialog"]')
        if confirmacao.count():
            try:
                confirmacao.last.get_by_role(
                    "button", name="Adicionar membros"
                ).click(timeout=3000)
                pagina.wait_for_timeout(2500)
            except PlaywrightTimeout:
                pass

        _fechar_avisos(pagina, registros)

        if mensagem:
            caixa = pagina.locator('[data-testid="conversation-compose-box-input"]')
            caixa.click()
            caixa.fill(mensagem)
            pagina.keyboard.press("Enter")
            pagina.wait_for_timeout(1200)
            registros.append("Mensagem enviada ao grupo.")

        return {"participantes": len(selecionados), "registros": registros}


def enviar_individual(numero, mensagem):
    registros = []
    telefone = re.sub(r"\D+", "", numero or "")
    texto = (mensagem or "").strip()
    if not telefone:
        raise RuntimeError("Candidato sem telefone válido.")
    if not texto:
        raise RuntimeError("Informe a mensagem individual.")

    with _pagina() as pagina:
        if not _esta_logado(pagina):
            raise RuntimeError("WhatsApp Web nao autenticado. Escaneie o QR code.")

        continuar = pagina.get_by_role("button", name="Continuar")
        if continuar.count():
            continuar.first.click()
            pagina.wait_for_timeout(600)

        pagina.locator('[aria-label="Nova conversa"]').click()
        pagina.wait_for_timeout(900)

        campo = None
        seletores = (
            '[data-testid="chat-list-search-input"] input',
            '[data-testid="chat-list-search-input"]',
            'input[aria-label*="Buscar"]',
            'input[placeholder*="Buscar"]',
            '[role="textbox"]',
        )
        for seletor in seletores:
            candidato = pagina.locator(seletor).first
            if candidato.count():
                campo = candidato
                break
        if campo is None:
            raise RuntimeError("Campo de busca de conversa nao encontrado.")

        termos = [telefone]
        if not telefone.startswith("55"):
            termos.append("55" + telefone)
        localizado = False
        for termo in termos:
            campo.fill(termo)
            pagina.wait_for_timeout(1800)
            resultados = pagina.locator(
                '[data-testid="cell-frame-container"][role="button"], [role="row"]'
            )
            if resultados.count():
                resultados.first.click()
                localizado = True
                registros.append(f"Conversa localizada: +{termo}")
                break
        if not localizado:
            raise RuntimeError("Numero nao encontrado no WhatsApp.")

        pagina.wait_for_timeout(1200)
        caixa = pagina.locator('[data-testid="conversation-compose-box-input"]')
        caixa.wait_for(state="visible", timeout=8000)
        caixa.fill(texto)
        pagina.keyboard.press("Enter")
        pagina.wait_for_timeout(1500)
        registros.append("Mensagem enviada ao candidato.")
        return {"enviado": True, "registros": registros}
