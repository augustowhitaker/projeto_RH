import os
import sqlite3
from flask import current_app, g
from werkzeug.security import generate_password_hash

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidatos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    telefone TEXT NOT NULL,
    cidade TEXT,
    email TEXT,
    origem TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'novo',
    curriculo TEXT,
    link TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS grupos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    participantes INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mensagens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    candidato_id INTEGER NOT NULL,
    telefone TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
    FOREIGN KEY (candidato_id) REFERENCES candidatos(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS configuracoes (
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def fecha_db(_erro=None):
    banco = g.pop("db", None)
    if banco is not None:
        banco.close()


def init_db():
    caminho = current_app.config["DATABASE"]
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    banco = sqlite3.connect(caminho)
    banco.executescript(ESQUEMA)
    colunas = {linha[1] for linha in banco.execute("PRAGMA table_info(candidatos)").fetchall()}
    novas_colunas = {
        "cpf": "TEXT NOT NULL DEFAULT ''",
        "estado": "TEXT NOT NULL DEFAULT ''",
        "endereco": "TEXT NOT NULL DEFAULT ''",
        "nascimento": "TEXT NOT NULL DEFAULT ''",
        "escolaridade": "TEXT NOT NULL DEFAULT ''",
        "experiencia": "TEXT NOT NULL DEFAULT ''",
        "vaga": "TEXT NOT NULL DEFAULT ''",
        "observacoes": "TEXT NOT NULL DEFAULT ''",
    }
    for nome, definicao in novas_colunas.items():
        if nome not in colunas:
            banco.execute(f"ALTER TABLE candidatos ADD COLUMN {nome} {definicao}")
    banco.execute(
        "UPDATE candidatos SET vaga = nome WHERE (vaga IS NULL OR vaga = '') AND nome <> ''"
    )
    email = current_app.config["ADMIN_EMAIL"]
    nome = current_app.config["ADMIN_NOME"]
    senha_hash = generate_password_hash(current_app.config["ADMIN_PASSWORD"])
    admin = banco.execute(
        "SELECT id FROM usuarios WHERE email = ? OR nome = ? ORDER BY id LIMIT 1",
        (email, nome),
    ).fetchone()
    if admin is None:
        admin = banco.execute("SELECT id FROM usuarios ORDER BY id LIMIT 1").fetchone()
    if admin is None:
        banco.execute(
            "INSERT INTO usuarios (nome, email, senha_hash) VALUES (?, ?, ?)",
            (nome, email, senha_hash),
        )
    else:
        banco.execute(
            "UPDATE usuarios SET nome = ?, email = ?, senha_hash = ? WHERE id = ?",
            (nome, email, senha_hash, admin[0]),
        )
    banco.commit()
    banco.close()
