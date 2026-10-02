"""API REST do MVP Alpha — Sistema de chamados acadêmicos com triagem híbrida.

Perfis: aluno | atendente (vinculado a um setor; setor 'Todos' enxerga tudo).
Autenticação: JWT (HS256) via PyJWT. Banco: SQLite (troque por PostgreSQL em produção).
"""
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import jwt
from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from app.triagem import SETORES, Triagem

BASE = Path(__file__).resolve().parent
STATUS_VALIDOS = ("aberto", "em_andamento", "resolvido")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
  senha_hash TEXT NOT NULL, papel TEXT NOT NULL CHECK (papel IN ('aluno','atendente')), setor TEXT);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  titulo TEXT NOT NULL, descricao TEXT NOT NULL, setor TEXT NOT NULL, urgencia TEXT NOT NULL,
  metodo TEXT NOT NULL, confianca REAL, revisao_manual INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'aberto', tempo_triagem_ms REAL, setor_original TEXT,
  criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS respostas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
  user_id INTEGER NOT NULL REFERENCES users(id), mensagem TEXT NOT NULL, criado_em TEXT NOT NULL);
"""

# Usuários de demonstração (apenas para apresentação; troque em produção)
SEED = [
    ("Aluno Demo", "aluno@unama.test", "aluno123", "aluno", None),
    ("Atendente Financeiro", "financeiro@unama.test", "atende123", "atendente", "Financeiro"),
    ("Atendente Secretaria", "secretaria@unama.test", "atende123", "atendente", "Secretaria"),
    ("Atendente Coordenação", "coordenacao@unama.test", "atende123", "atendente", "Coordenação"),
    ("Supervisor Geral", "supervisor@unama.test", "atende123", "atendente", "Todos"),
]


def agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_app(db_path=None, secret=None, seed=True):
    app = Flask(__name__, static_folder=None)
    app.config["DB_PATH"] = str(db_path or os.environ.get("DB_PATH", BASE.parent / "data" / "app.db"))
    app.config["SECRET_KEY"] = secret or os.environ.get("SECRET_KEY", "dev-secret-troque-em-producao-0123456789")
    app.triagem = Triagem.do_dataset()

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DB_PATH"], timeout=15)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys=ON")
        return g.db

    @app.teardown_appcontext
    def fechar(_):
        c = g.pop("db", None)
        if c is not None:
            c.close()

    # ------------------------------------------------------------ init db
    with sqlite3.connect(app.config["DB_PATH"]) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        if seed and c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            for nome, email, senha, papel, setor in SEED:
                c.execute("INSERT INTO users(nome,email,senha_hash,papel,setor) VALUES (?,?,?,?,?)",
                          (nome, email, generate_password_hash(senha), papel, setor))

    # ------------------------------------------------------------ auth
    def erro(msg, code):
        return jsonify({"erro": msg}), code

    def gerar_token(u):
        payload = {"sub": str(u["id"]), "papel": u["papel"], "setor": u["setor"],
                   "exp": datetime.now(timezone.utc) + timedelta(hours=8)}
        return jwt.encode(payload, app.config["SECRET_KEY"], algorithm="HS256")

    def autenticado(*papeis):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                h = request.headers.get("Authorization", "")
                if not h.startswith("Bearer "):
                    return erro("Token ausente", 401)
                try:
                    dados = jwt.decode(h[7:], app.config["SECRET_KEY"], algorithms=["HS256"])
                except jwt.PyJWTError:
                    return erro("Token inválido ou expirado", 401)
                u = db().execute("SELECT * FROM users WHERE id=?", (int(dados["sub"]),)).fetchone()
                if u is None:
                    return erro("Usuário não encontrado", 401)
                if papeis and u["papel"] not in papeis:
                    return erro("Acesso negado para este perfil", 403)
                g.user = u
                return fn(*a, **kw)
            return wrapper
        return deco

    def user_json(u):
        return {"id": u["id"], "nome": u["nome"], "email": u["email"], "papel": u["papel"], "setor": u["setor"]}

    def pode_ver(ticket, u):
        if u["papel"] == "aluno":
            return ticket["user_id"] == u["id"]
        return u["setor"] == "Todos" or u["setor"] == ticket["setor"]

    def ticket_json(row, com_respostas=False):
        d = {k: row[k] for k in ("id", "titulo", "descricao", "setor", "urgencia", "metodo", "confianca",
                                  "status", "tempo_triagem_ms", "setor_original", "criado_em", "atualizado_em")}
        d["revisao_manual"] = bool(row["revisao_manual"])
        d["aluno"] = db().execute("SELECT nome FROM users WHERE id=?", (row["user_id"],)).fetchone()["nome"]
        if com_respostas:
            rs = db().execute("""SELECT r.id, r.mensagem, r.criado_em, u.nome AS autor, u.papel AS papel
                                 FROM respostas r JOIN users u ON u.id=r.user_id
                                 WHERE r.ticket_id=? ORDER BY r.id""", (row["id"],)).fetchall()
            d["respostas"] = [dict(r) for r in rs]
        return d

    # ------------------------------------------------------------ rotas
    @app.get("/")
    def index():
        return send_from_directory(BASE / "static", "index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/api/auth/register")
    def register():
        d = request.get_json(silent=True) or {}
        nome, email, senha = (d.get("nome") or "").strip(), (d.get("email") or "").strip().lower(), d.get("senha") or ""
        if len(nome) < 2 or not EMAIL_RE.match(email) or len(senha) < 6:
            return erro("Informe nome, e-mail válido e senha com pelo menos 6 caracteres", 400)
        try:
            cur = db().execute("INSERT INTO users(nome,email,senha_hash,papel) VALUES (?,?,?,'aluno')",
                               (nome, email, generate_password_hash(senha)))
            db().commit()
        except sqlite3.IntegrityError:
            return erro("E-mail já cadastrado", 409)
        u = db().execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify({"token": gerar_token(u), "usuario": user_json(u)}), 201

    @app.post("/api/auth/login")
    def login():
        d = request.get_json(silent=True) or {}
        u = db().execute("SELECT * FROM users WHERE email=?", ((d.get("email") or "").strip().lower(),)).fetchone()
        if u is None or not check_password_hash(u["senha_hash"], d.get("senha") or ""):
            return erro("Credenciais inválidas", 401)
        return jsonify({"token": gerar_token(u), "usuario": user_json(u)})

    @app.get("/api/me")
    @autenticado()
    def me():
        return jsonify(user_json(g.user))

    @app.post("/api/triagem")
    @autenticado()
    def triagem_avulsa():
        """Simulação de triagem (não grava chamado) — útil para demonstração/testes."""
        d = request.get_json(silent=True) or {}
        texto = (d.get("texto") or "").strip()
        if not texto:
            return erro("Campo 'texto' é obrigatório", 400)
        modo = d.get("modo", "hibrido")
        if modo not in ("hibrido", "regras", "ml"):
            return erro("modo deve ser hibrido, regras ou ml", 400)
        return jsonify(app.triagem.triar(texto, modo))

    @app.post("/api/tickets")
    @autenticado("aluno")
    def criar_ticket():
        d = request.get_json(silent=True) or {}
        titulo, desc = (d.get("titulo") or "").strip(), (d.get("descricao") or "").strip()
        if len(titulo) < 3 or len(desc) < 10:
            return erro("Título (mín. 3) e descrição (mín. 10 caracteres) são obrigatórios", 400)
        if len(titulo) > 150 or len(desc) > 4000:
            return erro("Texto muito longo", 400)
        r = app.triagem.triar(f"{titulo}. {desc}")
        setor = r["setor"] if r["setor"] in SETORES else "Secretaria"  # 'indefinido' não ocorre no híbrido
        ts = agora()
        cur = db().execute(
            """INSERT INTO tickets(user_id,titulo,descricao,setor,urgencia,metodo,confianca,revisao_manual,
               status,tempo_triagem_ms,setor_original,criado_em,atualizado_em)
               VALUES (?,?,?,?,?,?,?,?, 'aberto', ?, ?, ?, ?)""",
            (g.user["id"], titulo, desc, setor, r["urgencia"], r["metodo"], r["confianca"],
             int(r["revisao_manual"]), r["tempo_ms"], setor, ts, ts))
        db().commit()
        row = db().execute("SELECT * FROM tickets WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(ticket_json(row, True)), 201

    @app.get("/api/tickets")
    @autenticado()
    def listar():
        u, sql, args = g.user, "SELECT * FROM tickets WHERE 1=1", []
        if u["papel"] == "aluno":
            sql += " AND user_id=?"; args.append(u["id"])
        elif u["setor"] != "Todos":
            sql += " AND setor=?"; args.append(u["setor"])
        elif request.args.get("setor") in SETORES:
            sql += " AND setor=?"; args.append(request.args["setor"])
        if request.args.get("status") in STATUS_VALIDOS:
            sql += " AND status=?"; args.append(request.args["status"])
        sql += " ORDER BY CASE urgencia WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, id DESC"
        rows = db().execute(sql, args).fetchall()
        return jsonify([ticket_json(r) for r in rows])

    def obter_ticket(tid):
        row = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        if row is None or not pode_ver(row, g.user):
            return None
        return row

    @app.get("/api/tickets/<int:tid>")
    @autenticado()
    def detalhe(tid):
        row = obter_ticket(tid)
        return jsonify(ticket_json(row, True)) if row else erro("Chamado não encontrado", 404)

    @app.post("/api/tickets/<int:tid>/respostas")
    @autenticado()
    def responder(tid):
        row = obter_ticket(tid)
        if row is None:
            return erro("Chamado não encontrado", 404)
        msg = ((request.get_json(silent=True) or {}).get("mensagem") or "").strip()
        if not msg:
            return erro("Mensagem vazia", 400)
        ts = agora()
        db().execute("INSERT INTO respostas(ticket_id,user_id,mensagem,criado_em) VALUES (?,?,?,?)",
                     (tid, g.user["id"], msg, ts))
        if g.user["papel"] == "atendente" and row["status"] == "aberto":
            db().execute("UPDATE tickets SET status='em_andamento' WHERE id=?", (tid,))
        db().execute("UPDATE tickets SET atualizado_em=? WHERE id=?", (ts, tid))
        db().commit()
        return jsonify(ticket_json(db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone(), True)), 201

    @app.patch("/api/tickets/<int:tid>")
    @autenticado("atendente")
    def atualizar(tid):
        row = obter_ticket(tid)
        if row is None:
            return erro("Chamado não encontrado", 404)
        d = request.get_json(silent=True) or {}
        if "status" in d:
            if d["status"] not in STATUS_VALIDOS:
                return erro("Status inválido", 400)
            db().execute("UPDATE tickets SET status=? WHERE id=?", (d["status"], tid))
        if "setor" in d:  # reencaminhamento (correção manual da triagem)
            if d["setor"] not in SETORES:
                return erro("Setor inválido", 400)
            db().execute("UPDATE tickets SET setor=? WHERE id=?", (d["setor"], tid))
        db().execute("UPDATE tickets SET atualizado_em=? WHERE id=?", (agora(), tid))
        db().commit()
        novo = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        return jsonify(ticket_json(novo, True))

    @app.get("/api/estatisticas")
    @autenticado("atendente")
    def estatisticas():
        u = g.user
        filtro, args = ("", []) if u["setor"] == "Todos" else (" WHERE setor=?", [u["setor"]])
        q = lambda sql: db().execute(sql.format(f=filtro), args).fetchall()
        return jsonify({
            "total": q("SELECT COUNT(*) c FROM tickets{f}")[0]["c"],
            "por_status": {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) c FROM tickets{f} GROUP BY status")},
            "por_setor": {r["setor"]: r["c"] for r in q("SELECT setor, COUNT(*) c FROM tickets{f} GROUP BY setor")},
            "por_urgencia": {r["urgencia"]: r["c"] for r in q("SELECT urgencia, COUNT(*) c FROM tickets{f} GROUP BY urgencia")},
            "tempo_medio_triagem_ms": q("SELECT ROUND(AVG(tempo_triagem_ms),3) m FROM tickets{f}")[0]["m"],
            "reencaminhados": q("SELECT COUNT(*) c FROM tickets" + (filtro + " AND" if filtro else " WHERE") +
                                " setor<>setor_original")[0]["c"],
        })

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    create_app().run(host="0.0.0.0", port=port, debug=False, threaded=True)
