import csv
import io
import os
import re
import unicodedata
from difflib import SequenceMatcher
from functools import wraps
from urllib.parse import urlencode, urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from openpyxl import Workbook, load_workbook
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from . import infojobs, scraping, whatsapp
from .db import get_db

bp = Blueprint("principal", __name__)


def login_obrigatorio(view):
    @wraps(view)
    def protegida(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("principal.login"))
        return view(*args, **kwargs)

    return protegida


def normalizar_telefone(valor):
    digitos = re.sub(r"\D", "", str(valor or ""))
    if digitos.startswith("55") and len(digitos) > 11:
        digitos = digitos[2:]
    return digitos


def normalizar_texto(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "").lower())
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def vaga_corresponde(valor, termo):
    alvo = normalizar_texto(valor)
    busca = normalizar_texto(termo)
    if not busca:
        return True
    if busca in alvo:
        return True
    termos = busca.split()
    alvos = alvo.split()
    for item in termos:
        if not any(
            item in parte or SequenceMatcher(None, item, parte).ratio() >= 0.78
            for parte in alvos
        ):
            return False
    return True


def dados_formulario_candidato():
    return {
        "nome": request.form.get("nome", "").strip(),
        "cpf": request.form.get("cpf", "").strip(),
        "telefone": normalizar_telefone(request.form.get("telefone", "")),
        "email": request.form.get("email", "").strip(),
        "cidade": request.form.get("cidade", "").strip(),
        "estado": request.form.get("estado", "").strip().upper(),
        "endereco": request.form.get("endereco", "").strip(),
        "nascimento": request.form.get("nascimento", "").strip(),
        "escolaridade": request.form.get("escolaridade", "").strip(),
        "experiencia": request.form.get("experiencia", "").strip(),
        "vaga": request.form.get("vaga", "").strip(),
        "observacoes": request.form.get("observacoes", "").strip(),
        "status": request.form.get("status", "novo").strip() or "novo",
    }


def salvar_curriculo(arquivo, ident):
    if not arquivo or not arquivo.filename:
        return None
    extensao = os.path.splitext(arquivo.filename)[1].lower()
    if extensao not in (".pdf", ".docx"):
        return None
    pasta = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, f"candidato_{ident}_{secure_filename(arquivo.filename)}")
    arquivo.save(caminho)
    return caminho


def gravar_candidato(ident=None):
    dados = dados_formulario_candidato()
    if not dados["nome"] or not dados["telefone"]:
        flash("Nome e telefone são obrigatórios.", "erro")
        return redirect(url_for("principal.candidatos"))
    banco = get_db()
    chaves = (
        "nome", "cpf", "telefone", "email", "cidade", "estado", "endereco",
        "nascimento", "escolaridade", "experiencia", "vaga", "observacoes", "status",
    )
    valores = tuple(dados[chave] for chave in chaves)
    if ident is None:
        marcadores = ", ".join("?" for _ in chaves)
        cursor = banco.execute(
            f"""INSERT INTO candidatos
                (usuario_id, {', '.join(chaves)}, origem)
                VALUES (?, {marcadores}, 'manual')""",
            (session["usuario_id"],) + valores,
        )
        ident = cursor.lastrowid
    else:
        atribuicoes = ", ".join(f"{chave} = ?" for chave in chaves)
        banco.execute(
            f"UPDATE candidatos SET {atribuicoes} WHERE id = ?",
            valores + (ident,),
        )
    caminho = salvar_curriculo(request.files.get("curriculo"), ident)
    if caminho:
        banco.execute("UPDATE candidatos SET curriculo = ? WHERE id = ?", (caminho, ident))
    banco.commit()
    flash("Candidato salvo com sucesso.", "sucesso")
    return redirect(url_for("principal.ver_candidato", ident=ident))


def resumo_agrupado(campo, limite=6):
    banco = get_db()
    linhas = banco.execute(
        f"""SELECT COALESCE(NULLIF({campo}, ''), 'Não informado') AS nome,
                   COUNT(*) AS total
            FROM candidatos GROUP BY nome ORDER BY total DESC LIMIT ?""",
        (limite,),
    ).fetchall()
    maior = max([linha["total"] for linha in linhas], default=1)
    return [
        {"nome": linha["nome"], "total": linha["total"], "percent": round(linha["total"] * 100 / maior)}
        for linha in linhas
    ]


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identificador = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        banco = get_db()
        usuario = banco.execute(
            "SELECT * FROM usuarios WHERE LOWER(email) = ? OR LOWER(nome) = ?",
            (identificador, identificador),
        ).fetchone()
        if usuario and check_password_hash(usuario["senha_hash"], senha):
            session.clear()
            session["usuario_id"] = usuario["id"]
            session["usuario_nome"] = usuario["nome"]
            if request.form.get("lembrar"):
                session.permanent = True
            return redirect(url_for("principal.painel"))
        flash("Usuário ou senha inválidos.", "erro")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("principal.login"))


@bp.route("/")
@login_obrigatorio
def painel():
    banco = get_db()
    total = banco.execute("SELECT COUNT(*) FROM candidatos").fetchone()[0]
    grupos = banco.execute("SELECT COUNT(*) FROM grupos").fetchone()[0]
    whats = banco.execute(
        "SELECT COUNT(*) FROM candidatos WHERE telefone <> ''"
    ).fetchone()[0]
    convocacoes = banco.execute(
        "SELECT COUNT(*) FROM candidatos WHERE status = 'convocado'"
    ).fetchone()[0]
    configuracao = banco.execute(
        "SELECT valor FROM configuracoes WHERE chave = 'vagas_abertas'"
    ).fetchone()
    vagas = int(configuracao["valor"] or 0) if configuracao else 0
    recentes = banco.execute(
        "SELECT * FROM candidatos ORDER BY id DESC LIMIT 5"
    ).fetchall()
    return render_template(
        "painel.html",
        total=total,
        grupos=grupos,
        whats=whats,
        convocacoes=convocacoes,
        vagas=vagas,
        recentes=recentes,
    )


@bp.route("/candidatos", methods=["GET", "POST"])
@login_obrigatorio
def candidatos():
    banco = get_db()
    if request.method == "POST":
        return redirect(url_for("principal.candidatos"))

    chaves = ("busca", "nome", "telefone", "cidade", "estado", "vaga", "escolaridade", "status", "periodo")
    filtros = {chave: request.args.get(chave, "").strip() for chave in chaves}
    clausulas = []
    parametros = []
    for chave in ("nome", "telefone", "cidade", "estado"):
        if filtros[chave]:
            clausulas.append(f"LOWER({chave}) LIKE ?")
            parametros.append(f"%{filtros[chave].lower()}%")
    if filtros["escolaridade"]:
        clausulas.append("escolaridade = ?")
        parametros.append(filtros["escolaridade"])
    if filtros["status"]:
        clausulas.append("status = ?")
        parametros.append(filtros["status"])
    if filtros["periodo"]:
        clausulas.append("date(criado_em) >= ?")
        parametros.append(filtros["periodo"])
    if filtros["busca"]:
        like = f"%{filtros['busca'].lower()}%"
        clausulas.append(
            "(LOWER(nome) LIKE ? OR telefone LIKE ? OR LOWER(cidade) LIKE ? OR LOWER(vaga) LIKE ?)"
        )
        parametros.extend((like, like, like, like))

    filtro_sql = " WHERE " + " AND ".join(clausulas) if clausulas else ""
    encontrados = banco.execute(
        f"SELECT * FROM candidatos{filtro_sql} ORDER BY id DESC", parametros
    ).fetchall()
    if filtros["vaga"]:
        encontrados = [
            linha for linha in encontrados
            if vaga_corresponde(f"{linha['vaga']} {linha['nome']}", filtros["vaga"])
        ]
    total = len(encontrados)
    por_pagina = 10
    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = max(1, request.args.get("pagina", 1, type=int) or 1)
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * por_pagina
    lista = encontrados[inicio:inicio + por_pagina]
    return render_template(
        "candidatos.html",
        candidatos=lista,
        filtros=filtros,
        total=total,
        pagina=pagina,
        total_paginas=total_paginas,
    )


@bp.route("/candidatos/novo", methods=["GET", "POST"])
@login_obrigatorio
def novo_candidato():
    if request.method == "POST":
        return gravar_candidato()
    return render_template(
        "novo_candidato.html",
        c=None,
        acao=url_for("principal.novo_candidato"),
    )


@bp.route("/candidatos/<int:ident>")
@login_obrigatorio
def ver_candidato(ident):
    candidato = get_db().execute("SELECT * FROM candidatos WHERE id = ?", (ident,)).fetchone()
    if not candidato:
        flash("Candidato não encontrado.", "erro")
        return redirect(url_for("principal.candidatos"))
    return render_template("ver_candidato.html", c=candidato)


@bp.route("/candidatos/<int:ident>/editar", methods=["GET", "POST"])
@login_obrigatorio
def editar_candidato(ident):
    banco = get_db()
    candidato = banco.execute("SELECT * FROM candidatos WHERE id = ?", (ident,)).fetchone()
    if not candidato:
        flash("Candidato não encontrado.", "erro")
        return redirect(url_for("principal.candidatos"))
    if request.method == "POST":
        return gravar_candidato(ident)
    return render_template(
        "editar_candidato.html",
        c=candidato,
        acao=url_for("principal.editar_candidato", ident=ident),
    )


@bp.route("/candidatos/<int:ident>/excluir", methods=["POST"])
@login_obrigatorio
def excluir_candidato(ident):
    banco = get_db()
    banco.execute("DELETE FROM candidatos WHERE id = ?", (ident,))
    banco.commit()
    flash("Candidato removido.", "sucesso")
    return redirect(url_for("principal.candidatos"))


def _coluna(cabecalho, *opcoes):
    for indice, nome in enumerate(cabecalho):
        texto = re.sub(r"\W+", "", str(nome or "").lower())
        if any(op in texto for op in opcoes):
            return indice
    return None


@bp.route("/candidatos/importar", methods=["GET", "POST"])
@login_obrigatorio
def importar():
    if request.method == "POST":
        arquivo = request.files.get("arquivo")
        if not arquivo or not arquivo.filename.lower().endswith((".xlsx", ".xlsm", ".csv")):
            flash("Envie um arquivo .xlsx ou .csv.", "erro")
            return redirect(url_for("principal.importar"))
        try:
            if arquivo.filename.lower().endswith(".csv"):
                texto = arquivo.read().decode("utf-8-sig", errors="replace")
                linhas = list(csv.reader(io.StringIO(texto)))
            else:
                planilha = load_workbook(io.BytesIO(arquivo.read()), read_only=True, data_only=True)
                linhas = [list(linha) for linha in planilha.active.values]
            if not linhas:
                raise ValueError("Planilha vazia")
            cabecalho = linhas[0]
            i_nome = _coluna(cabecalho, "nome", "candidato")
            i_tel = _coluna(cabecalho, "telefone", "fone", "celular", "whats")
            i_cid = _coluna(cabecalho, "cidade")
            i_mail = _coluna(cabecalho, "email")
            if i_nome is None or i_tel is None:
                raise ValueError("A planilha precisa das colunas Nome e Telefone")
            banco = get_db()
            inseridos = 0
            for linha in linhas[1:]:
                if i_nome >= len(linha) or i_tel >= len(linha):
                    continue
                nome = str(linha[i_nome] or "").strip()
                telefone = normalizar_telefone(linha[i_tel])
                if not nome or not telefone:
                    continue
                banco.execute(
                    """INSERT INTO candidatos
                       (usuario_id, nome, telefone, cidade, email, origem)
                       VALUES (?, ?, ?, ?, ?, 'excel')""",
                    (
                        session["usuario_id"],
                        nome,
                        telefone,
                        str(linha[i_cid] or "").strip() if i_cid is not None and i_cid < len(linha) else "",
                        str(linha[i_mail] or "").strip() if i_mail is not None and i_mail < len(linha) else "",
                    ),
                )
                inseridos += 1
            banco.commit()
            flash(f"{inseridos} candidatos importados.", "sucesso")
        except Exception as erro:
            flash(f"Falha na importação: {erro}", "erro")
        return redirect(url_for("principal.candidatos"))
    return render_template("importar.html")


@bp.route("/candidatos/exportar")
@login_obrigatorio
def exportar():
    banco = get_db()
    linhas = banco.execute("SELECT nome, telefone, cidade, email, origem FROM candidatos").fetchall()
    planilha = Workbook()
    aba = planilha.active
    aba.append(["Nome", "Telefone", "Cidade", "E-mail", "Origem"])
    for linha in linhas:
        aba.append(list(linha))
    saida = io.BytesIO()
    planilha.save(saida)
    saida.seek(0)
    return send_file(
        saida,
        as_attachment=True,
        download_name="candidatos_mendes_rh.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/scraping", methods=["GET", "POST"])
@login_obrigatorio
def pagina_scraping():
    resultados = []
    vagas = []
    area = ""
    form = request.form
    url = form.get("url_vaga", "").strip()
    conectado = infojobs.tem_cookie()
    if request.method == "POST":
        if not conectado:
            flash("Conecte sua conta InfoJobs Empresa em Configurações antes de buscar candidatos.", "erro")
        elif form.get("acao") == "descobrir" or not url:
            descoberta = infojobs.descobrir_vagas()
            vagas = descoberta.get("vagas", [])
            area = descoberta.get("area", "")
            if descoberta.get("erro"):
                flash(descoberta["erro"], "erro")
            elif vagas:
                flash(f"{len(vagas)} vaga(s) encontrada(s) na sua Área Empresa.", "sucesso")
            else:
                flash("Nenhuma vaga encontrada. Abra 'Descobrir minhas vagas' novamente.", "info")
        else:
            try:
                resultados = infojobs.buscar_inscritos(
                    url, int(form.get("limite", 30) or 30)
                )
                banco = get_db()
                salvos = 0
                for item in resultados:
                    link = item["link"] or ""
                    if link and banco.execute(
                        "SELECT 1 FROM candidatos WHERE link = ?", (link,)
                    ).fetchone():
                        continue
                    banco.execute(
                        """INSERT INTO candidatos
                           (usuario_id, nome, telefone, cidade, origem, link)
                           VALUES (?, ?, ?, ?, 'infojobs_empresa', ?)""",
                        (
                            session["usuario_id"],
                            item["nome"],
                            item["telefone"],
                            item["cidade"],
                            link,
                        ),
                    )
                    salvos += 1
                banco.commit()
                if resultados:
                    flash(
                        f"{len(resultados)} candidato(s) inscrito(s) encontrado(s). {salvos} novo(s) salvo(s).",
                        "sucesso",
                    )
                else:
                    flash("Nenhum inscrito encontrado nesta vaga.", "info")
            except Exception as erro:
                flash(f"Falha ao consultar inscritos: {erro}", "erro")

    return render_template(
        "scraping.html",
        resultados=resultados,
        vagas=vagas,
        area=area,
        url=url,
        conectado=conectado,
    )


@bp.route("/scraping/curriculo/<int:ident>")
@login_obrigatorio
def curriculo(ident):
    banco = get_db()
    candidato = banco.execute("SELECT * FROM candidatos WHERE id = ?", (ident,)).fetchone()
    if not candidato or not candidato["link"]:
        flash("Currículo indisponível.", "erro")
        return redirect(url_for("principal.candidatos"))
    if candidato["curriculo"] and os.path.exists(candidato["curriculo"]):
        return send_file(candidato["curriculo"], as_attachment=True)
    nome = secure_filename(re.sub(r"\W+", "_", candidato["nome"])) or "curriculo"
    caminho = os.path.join(current_app.config["UPLOAD_FOLDER"], f"{nome}_{ident}.pdf")
    try:
        scraping.baixar_curriculo(candidato["link"], caminho)
    except Exception as erro:
        flash(f"Não foi possível baixar: {erro}", "erro")
        return redirect(url_for("principal.candidatos"))
    banco.execute("UPDATE candidatos SET curriculo = ? WHERE id = ?", (caminho, ident))
    banco.commit()
    return send_file(caminho, as_attachment=True)


@bp.route("/whatsapp")
@login_obrigatorio
def pagina_whatsapp():
    if request.args.get("verificado") is None:
        try:
            situacao = whatsapp.status()
            situacao["verificado"] = True
        except Exception as erro:
            situacao = {"verificado": True, "logado": False, "erro": str(erro)}
    else:
        situacao = {
            "verificado": request.args.get("verificado") == "1",
            "logado": request.args.get("logado") == "1",
            "erro": None,
        }
    banco = get_db()
    grupos = banco.execute("SELECT * FROM grupos ORDER BY id DESC").fetchall()
    candidatos = banco.execute(
        "SELECT * FROM candidatos WHERE telefone <> '' ORDER BY id DESC"
    ).fetchall()
    mensagens = banco.execute(
        """SELECT m.*, c.nome AS candidato_nome
           FROM mensagens m LEFT JOIN candidatos c ON c.id = m.candidato_id
           ORDER BY m.id DESC"""
    ).fetchall()
    return render_template(
        "whatsapp.html",
        situacao=situacao,
        grupos=grupos,
        candidatos=candidatos,
        mensagens=mensagens,
        candidato_selecionado=request.args.get("candidato", type=int),
        mensagem=request.form.get("mensagem", ""),
    )


@bp.route("/whatsapp/status")
@login_obrigatorio
def status_whatsapp():
    try:
        situacao = whatsapp.status()
        logado = 1 if situacao.get("logado") else 0
    except Exception as erro:
        flash(f"Falha ao verificar o WhatsApp: {erro}", "erro")
        logado = 0
    return redirect(
        url_for("principal.pagina_whatsapp", verificado=1, logado=logado)
    )


@bp.route("/whatsapp/desconectar", methods=["POST"])
@login_obrigatorio
def desconectar_whatsapp():
    try:
        if whatsapp.desconectar():
            flash("WhatsApp Web desconectado. Escaneie o QR code para conectar novamente.")
        else:
            flash("Não foi possível desconectar o WhatsApp Web.", "erro")
    except Exception as erro:
        flash(f"Falha ao desconectar o WhatsApp: {erro}", "erro")
    return redirect(url_for("principal.pagina_whatsapp"))


@bp.route("/whatsapp/qr")
@login_obrigatorio
def whatsapp_qr():
    try:
        imagem = whatsapp.qr_code()
    except Exception as erro:
        return jsonify({"erro": str(erro)}), 500
    if imagem is None:
        return jsonify({"erro": "WhatsApp já está conectado"}), 400
    return send_file(imagem, mimetype="image/png")


@bp.route("/whatsapp/grupo", methods=["POST"])
@login_obrigatorio
def criar_grupo():
    ids = request.form.getlist("selecionados")
    nome = request.form.get("nome", "").strip()
    mensagem = request.form.get("mensagem", "").strip()
    if not nome or not ids:
        flash("Informe o nome do grupo e selecione candidatos.", "erro")
        return redirect(url_for("principal.pagina_whatsapp"))
    banco = get_db()
    numeros = []
    for ident in ids:
        candidato = banco.execute(
            "SELECT telefone FROM candidatos WHERE id = ?", (ident,)
        ).fetchone()
        if candidato:
            numeros.append(candidato["telefone"])
    try:
        resultado = whatsapp.criar_grupo(nome, numeros, mensagem)
        banco.execute(
            "INSERT INTO grupos (usuario_id, nome, participantes) VALUES (?, ?, ?)",
            (session["usuario_id"], nome, resultado["participantes"]),
        )
        banco.commit()
        flash(f"Grupo criado com {resultado['participantes']} participantes.", "sucesso")
        for registro in resultado["registros"]:
            flash(registro, "info")
    except Exception as erro:
        flash(f"Falha ao criar grupo: {erro}", "erro")
    return redirect(url_for("principal.pagina_whatsapp"))


@bp.route("/whatsapp/mensagem", methods=["POST"])
@login_obrigatorio
def enviar_mensagem():
    ident = request.form.get("candidato_id", "").strip()
    texto = request.form.get("mensagem", "").strip()
    if not ident or not texto:
        flash("Selecione um candidato e informe a mensagem.", "erro")
        return redirect(url_for("principal.pagina_whatsapp"))
    banco = get_db()
    candidato = banco.execute(
        "SELECT * FROM candidatos WHERE id = ?", (ident,)
    ).fetchone()
    if not candidato or not candidato["telefone"]:
        flash("Candidato sem telefone disponível.", "erro")
        return redirect(url_for("principal.pagina_whatsapp"))
    try:
        resultado = whatsapp.enviar_individual(candidato["telefone"], texto)
        banco.execute(
            """INSERT INTO mensagens
               (usuario_id, candidato_id, telefone, mensagem)
               VALUES (?, ?, ?, ?)""",
            (session["usuario_id"], candidato["id"], candidato["telefone"], texto),
        )
        banco.commit()
        flash(f"Mensagem enviada para {candidato['nome']}.", "sucesso")
        for registro in resultado["registros"]:
            flash(registro, "info")
    except Exception as erro:
        flash(f"Falha ao enviar mensagem: {erro}", "erro")
    return redirect(url_for("principal.pagina_whatsapp", candidato=ident))


@bp.route("/grupos")
@login_obrigatorio
def grupos():
    banco = get_db()
    lista = banco.execute(
        """SELECT g.*, u.nome AS usuario_nome
           FROM grupos g LEFT JOIN usuarios u ON u.id = g.usuario_id
           ORDER BY g.id DESC"""
    ).fetchall()
    return render_template("grupos.html", grupos=lista)


@bp.route("/mutiroes")
@login_obrigatorio
def mutiroes():
    return render_template("mutiroes.html")


@bp.route("/relatorios")
@login_obrigatorio
def relatorios():
    banco = get_db()
    total = banco.execute("SELECT COUNT(*) FROM candidatos").fetchone()[0]
    convocados = banco.execute(
        "SELECT COUNT(*) FROM candidatos WHERE status = 'convocado'"
    ).fetchone()[0]
    contratados = banco.execute(
        "SELECT COUNT(*) FROM candidatos WHERE status = 'contratado'"
    ).fetchone()[0]
    configuracao = banco.execute(
        "SELECT valor FROM configuracoes WHERE chave = 'vagas_abertas'"
    ).fetchone()
    vagas = int(configuracao["valor"] or 0) if configuracao else 0
    return render_template(
        "relatorios.html",
        total=total,
        vagas=vagas,
        taxa_convocacao=round(convocados * 100 / total) if total else 0,
        taxa_contratacao=round(contratados * 100 / total) if total else 0,
        por_cidade=resumo_agrupado("cidade"),
        por_vaga=resumo_agrupado("vaga"),
        por_origem=resumo_agrupado("origem", 4),
        por_status=resumo_agrupado("status", 8),
    )


@bp.route("/configuracoes", methods=["GET", "POST"])
@login_obrigatorio
def configuracoes():
    inspecao = None
    if request.method == "POST":
        if request.form.get("limpar_infojobs"):
            infojobs.remover_cookie()
            flash("Cookie do InfoJobs removido.", "sucesso")
            return redirect(url_for("principal.configuracoes"))
        if request.form.get("inspecionar_infojobs"):
            inspecao = infojobs.inspecionar()
            if inspecao.get("erro"):
                flash(inspecao["erro"], "erro")
            elif not inspecao.get("links"):
                flash("Nenhum link da Área Empresa foi encontrado. Confira o cookie.", "info")
            else:
                flash(f"{len(inspecao['links'])} link(s) da Área Empresa encontrado(s).", "sucesso")
        elif "cookie_infojobs" in request.form:
            valor = request.form.get("cookie_infojobs", "").strip()
            if valor:
                infojobs.salvar_cookie(valor)
                flash("Cookie da Área Empresa salvo com sucesso.", "sucesso")
            else:
                infojobs.remover_cookie()
                flash("Cookie do InfoJobs removido.", "sucesso")
            return redirect(url_for("principal.configuracoes"))
    return render_template(
        "configuracoes.html",
        infojobs_conectado=infojobs.tem_cookie(),
        inspecao=inspecao,
    )
