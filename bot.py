from __future__ import annotations

import os
import uuid
import getpass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_from_directory, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sys

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from database import ad_dict, get_connection, group_dict, init_db, row_dict, set_setting, setting
from meta_api import MetaAPI
from queue_manager import QueueManager
from poseidon_pay import PoseidonConfigurationError, PoseidonPaymentError, create_monthly_subscription
from subscriptions import has_active_subscription, create_pending_subscription, latest_subscription, mark_subscription_creation_failed, process_poseidon_webhook, update_subscription_from_creation
from flask_wtf.csrf import CSRFProtect

UPLOAD_FOLDER = BASE_DIR / "uploads"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")
if not app.config["SECRET_KEY"]:
    raise RuntimeError("FLASK_SECRET_KEY precisa estar definido no ambiente.")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV", "").lower() == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = 3600
csrf = CSRFProtect(app)

init_db()
queue = QueueManager(MetaAPI())

def is_safe_url(target):
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated_function


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().casefold()


def _is_admin_user(user_id: int) -> bool:
    configured_admin_email = _normalize_email(os.getenv("ADMIN_EMAIL"))
    if not configured_admin_email:
        return False
    with get_connection() as connection:
        user = connection.execute(
            "SELECT email FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
    return bool(user and _normalize_email(user["email"]) == configured_admin_email)


def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login", next=request.path))
        if _is_admin_user(user_id):
            return f(*args, **kwargs)
        if not has_active_subscription(user_id):
            return redirect(url_for("assinar"))
        return f(*args, **kwargs)
    return decorated_function



def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "true").lower() == "true"


def dashboard_data() -> dict:
    with get_connection() as connection:
        groups = [group_dict(row) for row in connection.execute("SELECT * FROM groups_table ORDER BY id DESC")]
        ads = [ad_dict(row, connection) for row in connection.execute("SELECT * FROM ads ORDER BY id DESC")]
        publications = [row_dict(row) for row in connection.execute(
            """SELECT p.*, g.name AS group_name, g.reference, a.title AS ad_title
               FROM publications p JOIN groups_table g ON g.id = p.group_id
               JOIN ads a ON a.id = p.ad_id ORDER BY p.id DESC"""
        )]
    stats = {
        "groups": len(groups),
        "published": sum(item["status"] == "published" for item in publications),
        "queued": sum(item["status"] == "queued" for item in publications),
        "errors": sum(item["status"] == "error" for item in publications),
    }
    return {"groups": groups, "ads": ads, "publications": publications, "stats": stats}


def _interval_label(seconds: int) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} segundo{'s' if seconds != 1 else ''}"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minuto{'s' if m != 1 else ''}"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hora{'s' if h != 1 else ''}"
    d = seconds // 86400
    return f"{d} dia{'s' if d != 1 else ''}"


import time as _time
_settings_cache: dict = {}
_settings_cache_ttl = 5  # seconds

def _cached_setting(key: str, default: str) -> str:
    now = _time.monotonic()
    entry = _settings_cache.get(key)
    if entry and now - entry[0] < _settings_cache_ttl:
        return entry[1]
    value = setting(key, default) or default
    _settings_cache[key] = (now, value)
    return value

def _invalidate_settings_cache():
    _settings_cache.clear()

app.jinja_env.filters["interval_label"] = _interval_label


@app.context_processor
def inject_globals():
    return {"dry_run": is_dry_run(), "queue_state": queue.state, "interval_seconds": int(_cached_setting("interval_seconds", "30"))}


@app.get("/")
@login_required
@subscription_required
def index():
    return render_template("index.html", **dashboard_data())


@app.route("/grupos", methods=["GET", "POST"])
@login_required
@subscription_required
def grupos():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        reference = request.form.get("reference", "").strip()
        if not name or not reference:
            flash("Informe o nome e a URL/ID do grupo.", "error")
        else:
            group_id = request.form.get("id")
            with get_connection() as connection:
                if group_id:
                    connection.execute("UPDATE groups_table SET name=?, reference=?, active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (name, reference, int(request.form.get("active") == "on"), group_id))
                else:
                    connection.execute("INSERT INTO groups_table(name, reference, active) VALUES (?, ?, ?)", (name, reference, int(request.form.get("active") == "on")))
            flash("Grupo salvo.", "success")
        return redirect(url_for("grupos"))
    with get_connection() as connection:
        groups = [group_dict(row) for row in connection.execute("SELECT * FROM groups_table ORDER BY id DESC")]
    edit_id = request.args.get("edit", type=int)
    editing = next((group for group in groups if group["id"] == edit_id), None)
    return render_template("grupos.html", groups=groups, editing=editing)


@app.post("/grupos/<int:group_id>/excluir")
@login_required
@subscription_required
def excluir_grupo(group_id: int):
    with get_connection() as connection:
        connection.execute("DELETE FROM groups_table WHERE id = ?", (group_id,))
    flash("Grupo excluído.", "success")
    return redirect(url_for("grupos"))


@app.route("/anuncios", methods=["GET", "POST"])
@login_required
@subscription_required
def anuncios():
    if request.method == "POST":
        fields = [request.form.get(field, "").strip() for field in ("title", "description", "price", "product_url")]
        if not all(fields):
            flash("Preencha título, descrição, preço e link do produto.", "error")
        else:
            ad_id = request.form.get("id")
            active = int(request.form.get("active") == "on")

            # Handle image upload
            image_url = request.form.get("image_url", "").strip()
            file = request.files.get("image_file")
            if file and file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit(".", 1)[1].lower()
                filename = f"{uuid.uuid4().hex}.{ext}"
                UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
                file.save(UPLOAD_FOLDER / filename)
                image_url = url_for("serve_upload", filename=filename, _external=False)
            elif request.form.get("remove_image") == "1":
                image_url = ""

            with get_connection() as connection:
                if ad_id:
                    connection.execute(
                        "UPDATE ads SET title=?, description=?, price=?, product_url=?, image_url=?, active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (*fields, image_url, active, ad_id),
                    )
                    connection.execute("DELETE FROM ad_variants WHERE ad_id=?", (ad_id,))
                else:
                    if hasattr(connection, 'dialect') and connection.dialect == 'postgres':
                        ad_id = connection.execute(
                            "INSERT INTO ads(title, description, price, product_url, image_url, active) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                            (*fields, image_url, active),
                        ).fetchone()["id"]
                    else:
                        ad_id = connection.execute(
                            "INSERT INTO ads(title, description, price, product_url, image_url, active) VALUES (?, ?, ?, ?, ?, ?)",
                            (*fields, image_url, active),
                        ).lastrowid
                variants = [line.strip() for line in request.form.getlist("variants") if line.strip()]
                connection.executemany("INSERT INTO ad_variants(ad_id, text) VALUES (?, ?)", [(ad_id, variant) for variant in variants])
            flash("Anúncio salvo.", "success")
        return redirect(url_for("anuncios"))
    with get_connection() as connection:
        ads = [ad_dict(row, connection) for row in connection.execute("SELECT * FROM ads ORDER BY id DESC")]
    edit_id = request.args.get("edit", type=int)
    editing = next((ad for ad in ads if ad["id"] == edit_id), None)
    return render_template("anuncios.html", ads=ads, editing=editing)


@app.get("/uploads/<path:filename>")
def serve_upload(filename: str):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.post("/anuncios/<int:ad_id>/excluir")
@login_required
@subscription_required
def excluir_anuncio(ad_id: int):
    with get_connection() as connection:
        connection.execute("DELETE FROM ads WHERE id = ?", (ad_id,))
    flash("Anúncio excluído.", "success")
    return redirect(url_for("anuncios"))


@app.route("/fila", methods=["GET", "POST"])
@login_required
@subscription_required
def fila():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "enqueue":
            ad_id = request.form.get("ad_id", type=int)
            times = request.form.get("times", type=int) or 3
            with get_connection() as connection:
                groups = connection.execute("SELECT id FROM groups_table WHERE active=1").fetchall()
                variants = connection.execute("SELECT id FROM ad_variants WHERE ad_id=? AND active=1 ORDER BY id", (ad_id,)).fetchall()
                for _ in range(times):
                    for index, group in enumerate(groups):
                        variant_id = variants[index % len(variants)]["id"] if variants else None
                        connection.execute("INSERT INTO publications(group_id, ad_id, variant_id, status) VALUES (?, ?, ?, 'queued')", (group["id"], ad_id, variant_id))
            flash(f"Fila criada para os grupos ativos ({times}x por grupo).", "success")
        elif action == "settings":
            seconds = max(5, int(request.form.get("interval_seconds", 30)))
            queue.set_interval(seconds)
            _invalidate_settings_cache()
            flash("Intervalo atualizado.", "success")
        else:
            {"start": queue.start, "pause": queue.pause, "continue": queue.start, "stop": queue.stop}.get(action, lambda: None)()
            flash("Estado da fila atualizado.", "success")
        return redirect(url_for("fila"))
    with get_connection() as connection:
        publications = [row_dict(row) for row in connection.execute("""SELECT p.*, g.name AS group_name, g.reference, a.title AS ad_title FROM publications p JOIN groups_table g ON g.id=p.group_id JOIN ads a ON a.id=p.ad_id ORDER BY p.id DESC""")]
        ads = [row_dict(row) for row in connection.execute("SELECT id, title FROM ads WHERE active=1 ORDER BY id DESC")]
    return render_template("fila.html", publications=publications, ads=ads)


@app.post("/publicacoes/<int:publication_id>/preparar")
@login_required
@subscription_required
def preparar_publicacao(publication_id: int):
    with get_connection() as connection:
        publication = connection.execute("""SELECT p.*, g.name AS group_name, g.reference, a.title, a.description, a.price, a.product_url, v.text AS variant_text FROM publications p JOIN groups_table g ON g.id=p.group_id JOIN ads a ON a.id=p.ad_id LEFT JOIN ad_variants v ON v.id=p.variant_id WHERE p.id=?""", (publication_id,)).fetchone()
        if not publication:
            return jsonify({"error": "Publicação não encontrada"}), 404
        item = row_dict(publication) or {}
        text = QueueManager.build_text(item)
        connection.execute("UPDATE publications SET status='prepared', result='Pronto para publicação manual', prepared_text=? WHERE id=?", (text, publication_id))
    return jsonify({"status": "prepared", "text": text, "group_reference": item["reference"]})


@app.get("/assinar")
@login_required
def assinar():
    if _is_admin_user(session["user_id"]):
        return redirect(url_for("index"))
    return render_template("assinar.html", subscription=latest_subscription(session["user_id"]), payment=None)


@app.post("/assinar/criar")
@login_required
def criar_assinatura():
    user_id = session["user_id"]
    if _is_admin_user(user_id):
        return redirect(url_for("index"))
    with get_connection() as connection:
        user = connection.execute(
            "SELECT id, name, email FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
    if not user or not user["name"] or not user["email"]:
        flash("Complete seu nome e e-mail antes de assinar.", "error")
        return redirect(url_for("assinar"))

    identifier = str(uuid.uuid4())
    subscription_id = create_pending_subscription(user_id, identifier)
    try:
        _, response = create_monthly_subscription(dict(user), identifier)
        update_subscription_from_creation(subscription_id, response)
    except (PoseidonConfigurationError, PoseidonPaymentError):
        mark_subscription_creation_failed(subscription_id)
        flash("Não foi possível criar a assinatura agora. Verifique a configuração de pagamento.", "error")
        return redirect(url_for("assinar"))
    except Exception as exc:
        mark_subscription_creation_failed(subscription_id)
        app.logger.error("Falha interna ao criar assinatura: %s", type(exc).__name__)
        flash("Não foi possível criar a assinatura agora.", "error")
        return redirect(url_for("assinar"))

    pix = response.get("pix") or {}
    payment = {
        "code": pix.get("code"),
        "image": pix.get("image"),
        "expires_at": pix.get("expiresAt"),
    }
    return render_template(
        "assinar.html",
        subscription=latest_subscription(user_id),
        payment=payment,
    )


@app.post("/webhooks/poseidon")
@csrf.exempt
def poseidon_webhook():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_payload"}), 400
    result, reason = process_poseidon_webhook(payload)
    event = payload.get("event", "unknown")
    transaction = payload.get("transaction") or {}
    transaction_id = transaction.get("id") if isinstance(transaction, dict) else None
    masked_transaction = f"...{str(transaction_id)[-6:]}" if transaction_id else "unknown"
    app.logger.info("Poseidon webhook event=%s transaction=%s result=%s", event, masked_transaction, result)
    if result == "unauthorized":
        return jsonify({"error": reason}), 401
    if result in {"invalid", "not_found", "rejected"}:
        return jsonify({"error": reason}), 400
    return jsonify({"ok": True, "result": result}), 200


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "dry_run": is_dry_run(), "queue_state": queue.state})

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        identifier = request.form.get("identifier", request.form.get("username", "")).strip()
        password = request.form.get("password", "")
        with get_connection() as connection:
            user = connection.execute(
                """SELECT id, username, password_hash, is_active FROM users
                   WHERE username = ? OR email = ?""",
                (identifier, identifier.lower()),
            ).fetchone()

        if user and user["is_active"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session.permanent = True
            next_url = request.args.get("next")
            return redirect(next_url if is_safe_url(next_url) else url_for("index"))

        flash("Usuário ou senha inválidos.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")

        if not all((name, email, username, password, password_confirmation)):
            flash("Preencha todos os campos.", "error")
        elif password != password_confirmation:
            flash("As senhas não coincidem.", "error")
        else:
            with get_connection() as connection:
                existing_email = connection.execute(
                    "SELECT 1 FROM users WHERE email = ?", (email,)
                ).fetchone()
                existing_username = connection.execute(
                    "SELECT 1 FROM users WHERE username = ?", (username,)
                ).fetchone()
                if existing_email:
                    flash("Este e-mail já está cadastrado.", "error")
                elif existing_username:
                    flash("Este usuário já está cadastrado.", "error")
                else:
                    connection.execute(
                        """INSERT INTO users(name, email, username, password_hash)
                           VALUES(?, ?, ?, ?)""",
                        (name, email, username, generate_password_hash(password)),
                    )
                    flash("Conta criada com sucesso.", "success")
                    return redirect(url_for("login"))

    return render_template("register.html")

@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "create-admin":
        if len(sys.argv) != 3:
            print("Uso: python bot.py create-admin <usuario>")
            sys.exit(1)

        username = sys.argv[2].strip()
        password = getpass.getpass("Senha do administrador: ")
        password_confirmation = getpass.getpass("Confirme a senha: ")
        if not username or not password or password != password_confirmation:
            print("Dados inválidos ou senhas diferentes.")
            sys.exit(1)
        hash_pw = generate_password_hash(password)
        with get_connection() as connection:
            try:
                connection.execute("INSERT INTO users(username, password_hash) VALUES(?, ?)", (username, hash_pw))
                print(f"Administrador '{username}' criado com sucesso!")
            except Exception:
                print("Não foi possível criar o administrador. Verifique se o usuário já existe.")
                sys.exit(1)
        sys.exit(0)
    
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_ENV") == "development")
