import os

from flask import Flask, redirect, url_for
from flask import request


def create_app():
    app = Flask(__name__)
    app.jinja_env.filters["combine"] = lambda atual, extra: {**atual, **extra}
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "mendes-rh-troque-esta-chave")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dados = "/data" if os.path.isdir("/data") else os.path.join(base, "data")
    os.makedirs(dados, exist_ok=True)
    app.config["DATABASE"] = os.getenv("DATABASE", os.path.join(dados, "mendesrh.db"))
    app.config["UPLOAD_FOLDER"] = os.path.join(dados, "uploads")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.config["ADMIN_EMAIL"] = os.getenv("ADMIN_EMAIL", "admin")
    app.config["ADMIN_PASSWORD"] = os.getenv("ADMIN_PASSWORD", "admin")
    app.config["ADMIN_NOME"] = os.getenv("ADMIN_NOME", "admin")
    app.config["WHATSAPP_CDP_URL"] = os.getenv("WHATSAPP_CDP_URL", "http://127.0.0.1:9222")
    app.config["WHATSAPP_USER_AGENT"] = os.getenv(
        "WHATSAPP_USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )

    from .db import fecha_db, init_db
    from .routes import bp

    app.teardown_appcontext(fecha_db)
    app.register_blueprint(bp)

    with app.app_context():
        init_db()

    @app.errorhandler(413)
    def muito_grande(_erro):
        return "Arquivo muito grande", 413

    return app
