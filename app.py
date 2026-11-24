import os
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "troque-esta-chave"),
    SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///crm.db").replace("postgres://", "postgresql://"),
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
)
db = SQLAlchemy(app)
login = LoginManager(app)
login.login_view = "entrar"

# Etapas do funil: da chegada do lead até a saída do paciente
STAGES = {
    "novo": "Novo lead",
    "followup1": "Follow-up 1",
    "followup2": "Follow-up 2",
    "followup3": "Follow-up 3",
    "atendimento": "Em atendimento",
    "paciente": "Paciente ativo",
    "pos_venda": "Pós-venda",
    "encerrado": "Encerrado",
}
POS_VENDA = ["Aguardando retorno", "Em acompanhamento", "Fidelizado", "Reativar"]

WA_TOKEN = os.getenv("WA_TOKEN")
WA_PHONE_ID = os.getenv("WA_PHONE_ID")
WA_VERIFY = os.getenv("WA_VERIFY_TOKEN", "verify-crm")


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)


class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), default="Sem nome")
    phone = db.Column(db.String(30), unique=True, nullable=False)
    stage = db.Column(db.String(20), default="novo")
    source = db.Column(db.String(60), default="WhatsApp")
    notes = db.Column(db.Text, default="")
    entered_at = db.Column(db.DateTime, default=datetime.utcnow)
    exited_at = db.Column(db.DateTime)
    exit_reason = db.Column(db.String(200))
    messages = db.relationship("Message", backref="lead", order_by="Message.created_at")
    events = db.relationship("Event", backref="lead", order_by="Event.created_at.desc()")
    sales = db.relationship("Sale", backref="lead", order_by="Sale.sold_at.desc()")


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.ForeignKey("lead.id"), nullable=False)
    direction = db.Column(db.String(3))  # in / out
    body = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Event(db.Model):  # linha do tempo do paciente
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.ForeignKey("lead.id"), nullable=False)
    text = db.Column(db.String(300))
    by = db.Column(db.String(120), default="Sistema")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.ForeignKey("lead.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    sold_at = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200))
    classification = db.Column(db.String(40), default=POS_VENDA[0])


class Campaign(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    template_name = db.Column(db.String(120), nullable=False)  # template aprovado na Meta
    target_stage = db.Column(db.String(20), nullable=False)
    sent_count = db.Column(db.Integer, default=0)
    last_run = db.Column(db.DateTime)


@login.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


def log(lead, text):
    db.session.add(Event(lead_id=lead.id, text=text,
                         by=current_user.name if current_user.is_authenticated else "Sistema"))


def clean_phone(p):
    return "".join(c for c in (p or "") if c.isdigit())


def wa_send(phone, payload):
    """Envia pela API oficial da Meta. Sem credenciais, apenas simula (modo teste)."""
    if not (WA_TOKEN and WA_PHONE_ID):
        return True
    r = requests.post(f"https://graph.facebook.com/v20.0/{WA_PHONE_ID}/messages",
                      headers={"Authorization": f"Bearer {WA_TOKEN}"},
                      json={"messaging_product": "whatsapp", "to": phone, **payload}, timeout=15)
    return r.ok


# ---------- Acesso ----------
@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        code = os.getenv("REGISTER_CODE")
        f = request.form
        if code and f.get("code") != code:
            flash("Código de convite inválido.")
        elif User.query.filter_by(email=f["email"].lower()).first():
            flash("Este e-mail já está cadastrado.")
        elif len(f["password"]) < 8:
            flash("A senha precisa ter pelo menos 8 caracteres.")
        else:
            u = User(name=f["name"], email=f["email"].lower(),
                     password_hash=generate_password_hash(f["password"]),
                     is_admin=User.query.count() == 0)
            db.session.add(u)
            db.session.commit()
            login_user(u)
            return redirect(url_for("leads"))
    return render_template("auth.html", mode="cadastro", need_code=bool(os.getenv("REGISTER_CODE")))


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    if request.method == "POST":
        u = User.query.filter_by(email=request.form["email"].lower()).first()
        if u and check_password_hash(u.password_hash, request.form["password"]):
            login_user(u)
            return redirect(url_for("leads"))
        flash("E-mail ou senha incorretos.")
    return render_template("auth.html", mode="entrar")


@app.route("/sair")
def sair():
    logout_user()
    return redirect(url_for("entrar"))


# ---------- Central de leads ----------
@app.route("/")
@login_required
def leads():
    q = request.args.get("q", "").strip()
    query = Lead.query
    if q:
        query = query.filter(Lead.name.ilike(f"%{q}%") | Lead.phone.contains(clean_phone(q) or q))
    cols = {k: [] for k in STAGES}
    for l in query.order_by(Lead.entered_at.desc()):
        cols[l.stage].append(l)
    return render_template("leads.html", cols=cols, stages=STAGES, q=q)


@app.route("/lead/novo", methods=["POST"])
@login_required
def lead_novo():
    phone = clean_phone(request.form["phone"])
    if not phone or Lead.query.filter_by(phone=phone).first():
        flash("Telefone vazio ou já cadastrado.")
        return redirect(url_for("leads"))
    l = Lead(name=request.form["name"] or "Sem nome", phone=phone, source=request.form.get("source") or "Manual")
    db.session.add(l)
    db.session.flush()
    log(l, f"Lead cadastrado ({l.source})")
    db.session.commit()
    return redirect(url_for("lead", id=l.id))


@app.route("/lead/<int:id>")
@login_required
def lead(id):
    return render_template("lead.html", l=db.get_or_404(Lead, id), stages=STAGES,
                           pos=POS_VENDA, today=datetime.utcnow().date())


@app.route("/lead/<int:id>/etapa", methods=["POST"])
@login_required
def lead_etapa(id):
    l = db.get_or_404(Lead, id)
    new = request.form["stage"]
    if new in STAGES and new != l.stage:
        old = l.stage
        l.stage = new
        if new == "encerrado":
            l.exited_at = datetime.utcnow()
            l.exit_reason = request.form.get("reason", "")
        else:
            l.exited_at = None
        log(l, f"Etapa: {STAGES[old]} → {STAGES[new]}" + (f" ({l.exit_reason})" if new == "encerrado" and l.exit_reason else ""))
        db.session.commit()
    return redirect(url_for("lead", id=id))


@app.route("/lead/<int:id>/notas", methods=["POST"])
@login_required
def lead_notas(id):
    l = db.get_or_404(Lead, id)
    l.notes = request.form["notes"]
    db.session.commit()
    return redirect(url_for("lead", id=id))


@app.route("/lead/<int:id>/enviar", methods=["POST"])
@login_required
def lead_enviar(id):
    l = db.get_or_404(Lead, id)
    body = request.form["body"].strip()
    if body:
        # Texto livre só funciona na janela de 24h após a última mensagem do lead
        if wa_send(l.phone, {"type": "text", "text": {"body": body}}):
            db.session.add(Message(lead_id=l.id, direction="out", body=body))
        else:
            flash("Não foi possível enviar. Fora da janela de 24h, use um template em Campanhas.")
        db.session.commit()
    return redirect(url_for("lead", id=id))


# ---------- Vendas / pós-venda ----------
@app.route("/lead/<int:id>/venda", methods=["POST"])
@login_required
def venda_nova(id):
    l = db.get_or_404(Lead, id)
    f = request.form
    s = Sale(lead_id=l.id, amount=f["amount"].replace(",", "."), sold_at=datetime.strptime(f["sold_at"], "%Y-%m-%d").date(),
             description=f.get("description"), classification=f.get("classification", POS_VENDA[0]))
    db.session.add(s)
    l.stage = "pos_venda"
    log(l, f"Venda registrada: R$ {s.amount} em {s.sold_at:%d/%m/%Y}")
    db.session.commit()
    return redirect(url_for("lead", id=id))


@app.route("/venda/<int:id>/classificar", methods=["POST"])
@login_required
def venda_classificar(id):
    s = db.get_or_404(Sale, id)
    s.classification = request.form["classification"]
    log(s.lead, f"Pós-venda: {s.classification}")
    db.session.commit()
    return redirect(request.referrer or url_for("vendas"))


@app.route("/vendas")
@login_required
def vendas():
    sales = Sale.query.order_by(Sale.sold_at.desc()).all()
    total = sum(float(s.amount) for s in sales)
    return render_template("vendas.html", sales=sales, total=total, pos=POS_VENDA)


# ---------- Campanhas ----------
@app.route("/campanhas", methods=["GET", "POST"])
@login_required
def campanhas():
    if request.method == "POST":
        f = request.form
        db.session.add(Campaign(name=f["name"], template_name=f["template_name"], target_stage=f["target_stage"]))
        db.session.commit()
        return redirect(url_for("campanhas"))
    return render_template("campanhas.html", camps=Campaign.query.order_by(Campaign.id.desc()).all(), stages=STAGES)


@app.route("/campanhas/<int:id>/disparar", methods=["POST"])
@login_required
def campanha_disparar(id):
    c = db.get_or_404(Campaign, id)
    sent = 0
    for l in Lead.query.filter_by(stage=c.target_stage):
        if wa_send(l.phone, {"type": "template", "template": {"name": c.template_name, "language": {"code": "pt_BR"}}}):
            db.session.add(Message(lead_id=l.id, direction="out", body=f"[Campanha: {c.name}]"))
            log(l, f"Recebeu campanha “{c.name}”")
            sent += 1
    c.sent_count += sent
    c.last_run = datetime.utcnow()
    db.session.commit()
    flash(f"Campanha enviada para {sent} contato(s).")
    return redirect(url_for("campanhas"))


# ---------- Webhook do WhatsApp (Meta Cloud API) ----------
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == WA_VERIFY:
            return request.args.get("hub.challenge", ""), 200
        return "forbidden", 403
    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value", {})
            names = {c["wa_id"]: c.get("profile", {}).get("name") for c in v.get("contacts", [])}
            for m in v.get("messages", []):
                phone = clean_phone(m["from"])
                l = Lead.query.filter_by(phone=phone).first()
                if not l:
                    l = Lead(phone=phone, name=names.get(phone) or "Sem nome")
                    db.session.add(l)
                    db.session.flush()
                    log(l, "Lead chegou pelo WhatsApp")
                elif l.stage in ("followup1", "followup2", "followup3"):
                    l.stage = "atendimento"
                    log(l, "Lead respondeu e voltou para atendimento")
                elif l.stage == "encerrado":
                    l.stage, l.exited_at = "novo", None
                    log(l, "Contato retornou após encerramento")
                text = m.get("text", {}).get("body") or f"[{m.get('type')}]"
                db.session.add(Message(lead_id=l.id, direction="in", body=text))
    db.session.commit()
    return "ok", 200


@app.route("/health")
def health():
    return "ok"


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, port=8080)
