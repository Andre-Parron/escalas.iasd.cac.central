"""Escalas de Voluntários — backend Flask + SQLite.

Sem contas: voluntários se identificam pelo nome; líderes entram pelo nome
(tabela `admins`) e recebem uma chave de sessão; confirmações só por link com token.
"""
import json, os, re, secrets, sqlite3, time, unicodedata, urllib.error, urllib.request, datetime as dt
from contextlib import contextmanager
from flask import Flask, jsonify, request, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
PG = DATABASE_URL.startswith("postgres")  # Postgres (ex.: Neon na Vercel); senão SQLite
EM_VERCEL = bool(os.environ.get("VERCEL"))
DB_PATH = os.environ.get("DB_PATH", "/tmp/escalas.db" if EM_VERCEL else os.path.join(BASE, "escalas.db"))
PERSISTENTE = PG or not EM_VERCEL  # SQLite em /tmp (Vercel) é apagado a qualquer momento
if PG:
    import psycopg
    from psycopg.rows import dict_row
MINISTERIOS = {
    "Mídia": ["Fotografia/Filmagem", "Design/Arte",],
    "Transmissão": ["Live/YouTube", "Câmeras"],
    "Sonoplastia": ["Mesa de som", "Projeção"],
}
FUNCOES = {f for fs in MINISTERIOS.values() for f in fs}
DIAS_CULTO = {0, 3, 6}  # domingo, quarta, sábado (0 = domingo, como no JavaScript)
STATUS = {"pendente", "aceito", "recusado", "faltou"}
SESSAO_SEG = 12 * 3600
DIAS_NOME = ["Domingo", "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado"]

# WhatsApp Cloud API (Meta). Sem WHATSAPP_TOKEN e WHATSAPP_PHONE_ID o envio automático fica desligado.
WA_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WA_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
WA_TEMPLATE = os.environ.get("WHATSAPP_TEMPLATE", "escala_voluntario")
WA_LANG = os.environ.get("WHATSAPP_LANG", "pt_BR")
WA_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v23.0")
WA_OK = bool(WA_TOKEN and WA_PHONE_ID)

# Telegram: avisos para o líder (aceite, recusa, pedido de troca). Só o token é obrigatório;
# o chat é descoberto pelo botão "Conectar" do painel (ou fixado em TELEGRAM_CHAT_ID).
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_EVENTOS = {x.strip() for x in os.environ.get("TELEGRAM_EVENTOS", "aceito,recusado,troca").split(",") if x.strip()}

app = Flask(__name__, static_folder="public", static_url_path="")

SCHEMA = """
create table if not exists admins(nome text primary key);
create table if not exists voluntarios(id text primary key, nome text not null, zap text default '',
  mail text default '', obs text default '', funcs text not null, disp text not null);
create table if not exists cultos(id text primary key, data text not null, dia integer not null,
  hora text not null, tipo text not null);
create table if not exists escalas(id text primary key, culto text not null, vol text not null,
  ministerio text not null, funcao text not null, st text not null default 'pendente', tok text unique not null);
create table if not exists avisos(id text primary key, vol text, esc text, msg text, d bigint);
create table if not exists config(k text primary key, v text);
create table if not exists envios(esc text, t double precision, ok integer, detalhe text);
create table if not exists tentativas(chave text, ip text, t double precision);
create table if not exists sessoes(chave text primary key, nome text, exp double precision);
"""


class Conn:
    """Conexão fina: SQLite (local/Docker) ou Postgres (DATABASE_URL)."""

    def __init__(self):
        if PG:
            self.c = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row,
                                     prepare_threshold=None, connect_timeout=10)
        else:
            self.c = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)
            self.c.row_factory = sqlite3.Row

    def execute(self, sql, p=()):
        return self.c.execute(sql.replace("?", "%s"), p or None) if PG else self.c.execute(sql, p)

    def executemany(self, sql, rows):
        if PG:
            with self.c.cursor() as cur:
                cur.executemany(sql.replace("?", "%s"), rows)
        else:
            self.c.executemany(sql, rows)

    def close(self):
        self.c.close()


@contextmanager
def tx():
    c = Conn()
    try:
        c.execute("begin" if PG else "begin immediate")
        if PG:
            c.execute("select pg_advisory_xact_lock(42)")  # um escritor por vez
        yield c
        c.execute("commit")
    except BaseException:
        c.execute("rollback")
        raise
    finally:
        c.close()


def init_db():
    c = Conn()
    try:
        if PG:  # transação + lock de transação: seguro com pooler (pgbouncer) e várias instâncias
            c.execute("begin")
            c.execute("select pg_advisory_xact_lock(41)")
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                c.execute(stmt)
        if not c.execute("select 1 from admins").fetchone():
            for n in os.environ.get("ADMINS", "Líder Demo").split(","):
                if n.strip():
                    c.execute("insert into admins values(?) on conflict do nothing", (n.strip(),))
        c.execute("insert into config values('limite','4') on conflict do nothing")
        c.execute("insert into config values('versao','1') on conflict do nothing")
        if PG:
            c.execute("commit")
    except BaseException:
        if PG:
            c.execute("rollback")
        raise
    finally:
        c.close()


_pronto = False


@app.before_request
def _garantir_db():
    global _pronto
    if not _pronto and request.path.startswith("/api/"):
        init_db()
        _pronto = True


def norm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    return re.sub(r"\s+", " ", "".join(ch for ch in s if not unicodedata.combining(ch))).strip().lower()


def clean(s, n=200):
    return str(s or "").strip()[:n]


def cid(x):
    return x if isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9]{1,24}", x) else None


def uid():
    return secrets.token_hex(4)


def bump(c):
    c.execute("update config set v=cast(cast(v as integer)+1 as text) where k='versao'")


def versao(c):
    return int(c.execute("select v from config where k='versao'").fetchone()["v"])


def limitar(chave, n, janela):
    """Limite de tentativas por IP, guardado no banco (vale entre instâncias serverless)."""
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "?"
    agora = time.time()
    c = Conn()
    try:
        if secrets.randbelow(20) == 0:
            c.execute("delete from tentativas where t<?", (agora - 86400,))
        r = c.execute("select count(*) as n from tentativas where chave=? and ip=? and t>?", (chave, ip, agora - janela)).fetchone()
        if r["n"] >= n:
            raise Erro("Muitas tentativas. Aguarde um pouco.", 429)
        c.execute("insert into tentativas values(?,?,?)", (chave, ip, agora))
    finally:
        c.close()


class Erro(Exception):
    def __init__(self, msg, code=400):
        self.msg, self.code = msg, code


@app.errorhandler(Erro)
def _erro(e):
    return jsonify(erro=e.msg), e.code


@app.after_request
def _headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/") or request.path in ("/", "/index.html"):
        r.headers["Cache-Control"] = "no-store"
    return r


def e_admin(c):
    k = request.headers.get("X-Admin-Key", "")
    return bool(k) and c.execute("select 1 from sessoes where chave=? and exp>?", (k, time.time())).fetchone() is not None


def montar(c, admin):
    vols = []
    for r in c.execute("select * from voluntarios order by lower(nome)"):
        v = {"id": r["id"], "nome": r["nome"], "obs": r["obs"], "funcs": json.loads(r["funcs"]), "disp": json.loads(r["disp"])}
        if admin:
            v.update(zap=r["zap"], mail=r["mail"])
        vols.append(v)
    esc = []
    for r in c.execute("select * from escalas"):
        e = {"id": r["id"], "culto": r["culto"], "vol": r["vol"], "min": r["ministerio"], "func": r["funcao"], "st": r["st"]}
        if admin:
            e["tok"] = r["tok"]
        esc.append(e)
    s = {"vols": vols, "esc": esc, "lim": int(c.execute("select v from config where k='limite'").fetchone()["v"]),
         "cultos": [dict(r) for r in c.execute("select * from cultos order by data, hora")]}
    if admin:
        s["admins"] = [r["nome"] for r in c.execute("select nome from admins order by nome")]
        s["av"] = [dict(r) for r in c.execute("select * from avisos order by d")]
        s["envios"] = {r["esc"]: {"t": r["t"], "ok": bool(r["ok"]), "msg": r["detalhe"]}
                       for r in c.execute("select esc, t, ok, detalhe from envios order by t")}
    return s


def validar_voluntario(d):
    nome = clean(d.get("nome"), 80)
    funcs = [f for f in d.get("funcs", []) if f in FUNCOES] if isinstance(d.get("funcs"), list) else []
    disp = [x for x in d.get("disp", []) if x in DIAS_CULTO] if isinstance(d.get("disp"), list) else []
    if not nome:
        raise Erro("Informe o nome.")
    if not funcs:
        raise Erro("Marque ao menos uma função.")
    if not disp:
        raise Erro("Marque ao menos um dia disponível.")
    return dict(nome=nome, zap=clean(d.get("zap"), 30), mail=clean(d.get("mail"), 120), obs=clean(d.get("obs"), 200),
                funcs=json.dumps(sorted(set(funcs))), disp=json.dumps(sorted(set(disp))))


def aplicar(c, s):
    """Valida o estado enviado pelo líder e substitui as tabelas."""
    if not isinstance(s, dict):
        raise Erro("Formato inválido.")
    admins = list(dict.fromkeys(clean(a, 80) for a in s.get("admins", []) if clean(a, 80)))
    if not admins:
        raise Erro("É preciso manter ao menos um líder.", 422)
    vols, vids = [], set()
    for v in s.get("vols", []):
        i = cid(v.get("id"))
        if not i or i in vids:
            raise Erro("Voluntário inválido.", 422)
        vids.add(i)
        vols.append((i, validar_voluntario(v)))
    if len({norm(v["nome"]) for _, v in vols}) != len(vols):
        raise Erro("Há voluntários com nomes repetidos.", 422)
    cultos, cmap = [], {}
    for x in s.get("cultos", []):
        i = cid(x.get("id"))
        try:
            data = dt.date.fromisoformat(str(x.get("data")))
        except ValueError:
            raise Erro("Data de culto inválida.", 422)
        dia = (data.weekday() + 1) % 7
        if not i or i in cmap or dia not in DIAS_CULTO or not re.fullmatch(r"\d{2}:\d{2}", str(x.get("hora", ""))):
            raise Erro("Culto inválido (só domingo, quarta ou sábado).", 422)
        cmap[i] = (data.isoformat(), dia, x["hora"])
        cultos.append((i, data.isoformat(), dia, x["hora"], clean(x.get("tipo"), 80) or "Culto"))
    escs, eids, slots, toks = [], set(), {}, set()
    for e in s.get("esc", []):
        i, m, f = cid(e.get("id")), e.get("min"), e.get("func")
        if not i or i in eids or e.get("culto") not in cmap or e.get("vol") not in vids:
            raise Erro("Escala inválida.", 422)
        if m not in MINISTERIOS or f not in MINISTERIOS[m] or e.get("st") not in STATUS:
            raise Erro("Escala inválida.", 422)
        data, dia, hora = cmap[e["culto"]]
        if m == "Transmissão" and dia != 6:
            raise Erro("Transmissão só existe nos cultos de sábado.", 422)
        if e["st"] != "recusado":
            k = (e["vol"], data, hora)
            if k in slots:
                raise Erro("Conflito: a mesma pessoa em dois lugares no mesmo horário.", 422)
            slots[k] = 1
        tok = e.get("tok") if re.fullmatch(r"[0-9a-f]{24}", str(e.get("tok", ""))) and e["tok"] not in toks else secrets.token_hex(12)
        toks.add(tok)
        eids.add(i)
        escs.append((i, e["culto"], e["vol"], m, f, e["st"], tok))
    avs = [(cid(a.get("id")) or uid(), a.get("vol"), a.get("esc"), clean(a.get("msg"), 300), int(a.get("d") or time.time() * 1000))
           for a in s.get("av", []) if isinstance(a, dict)]
    lim = min(99, max(1, int(s.get("lim") or 4)))
    for t in ("admins", "voluntarios", "cultos", "escalas", "avisos"):
        c.execute(f"delete from {t}")
    c.executemany("insert into admins values(?)", [(a,) for a in admins])
    c.executemany("insert into voluntarios values(?,?,?,?,?,?,?)",
                  [(i, v["nome"], v["zap"], v["mail"], v["obs"], v["funcs"], v["disp"]) for i, v in vols])
    c.executemany("insert into cultos values(?,?,?,?,?)", cultos)
    c.executemany("insert into escalas values(?,?,?,?,?,?,?)", escs)
    c.executemany("insert into avisos values(?,?,?,?,?)", avs)
    c.execute("update config set v=? where k='limite'", (str(lim),))
    bump(c)


def _txt(x, n=60):
    """Parâmetros de template não aceitam quebra de linha/tabulação."""
    return re.sub(r"\s+", " ", str(x or "")).strip()[:n] or "-"


def wa_enviar(zap, nome, resumo, quando, link):
    """Envia o template de escala pela WhatsApp Cloud API. Levanta Erro com mensagem amigável."""
    num = re.sub(r"\D", "", zap or "")
    if not num:
        raise Erro("Voluntário sem WhatsApp cadastrado.")
    if len(num) <= 11:  # DDD + número, sem código do país
        num = "55" + num
    corpo = {"messaging_product": "whatsapp", "to": num, "type": "template",
             "template": {"name": WA_TEMPLATE, "language": {"code": WA_LANG},
                          "components": [{"type": "body", "parameters": [
                              {"type": "text", "text": _txt(v, 200 if i == 3 else 60)}
                              for i, v in enumerate((nome, resumo, quando, link))]}]}}
    req = urllib.request.Request(
        f"https://graph.facebook.com/{WA_VERSION}/{WA_PHONE_ID}/messages", data=json.dumps(corpo).encode(),
        headers={"Authorization": "Bearer " + WA_TOKEN, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise Erro("WhatsApp recusou o envio: " + (msg or f"erro {e.code}"), 502)
    except Exception:
        raise Erro("Não foi possível falar com o WhatsApp. Tente de novo.", 502)


@app.post("/api/whatsapp/enviar")
def whatsapp_enviar():
    d = request.get_json(silent=True) or {}
    ids = [i for i in (d.get("esc") or []) if cid(i)][:50] if isinstance(d.get("esc"), list) else []
    base = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not base and re.fullmatch(r"https?://[\w.\-]+(:\d+)?", str(d.get("base", ""))):
        base = d["base"]
    c = Conn()
    try:
        if not e_admin(c):
            raise Erro("Sessão de líder expirada. Entre novamente.", 401)
        if not WA_OK:
            raise Erro("WhatsApp automático não está configurado no servidor.")
        if not base:
            raise Erro("Endereço público do site desconhecido (defina PUBLIC_URL).")
        resultados = []
        for i in ids:
            r = c.execute("select e.id, e.ministerio, e.funcao, e.tok, v.nome, v.zap, k.data, k.dia, k.hora "
                          "from escalas e join voluntarios v on v.id=e.vol join cultos k on k.id=e.culto where e.id=?", (i,)).fetchone()
            if not r:
                resultados.append({"id": i, "ok": False, "erro": "Escala não encontrada."})
                continue
            quando = f"{DIAS_NOME[r['dia']]} {r['data'][8:]}/{r['data'][5:7]} às {r['hora']}"
            try:
                wa_enviar(r["zap"], r["nome"], f"{r['ministerio']} - {r['funcao']}", quando, f"{base}/#t={r['tok']}")
                ok, detalhe = 1, "enviado"
            except Erro as e:
                ok, detalhe = 0, e.msg
            c.execute("insert into envios values(?,?,?,?)", (i, time.time(), ok, detalhe[:200]))
            resultados.append({"id": i, "ok": bool(ok), "erro": None if ok else detalhe})
        return jsonify(resultados=resultados)
    finally:
        c.close()


def exigir_admin():
    c = Conn()
    try:
        if not e_admin(c):
            raise Erro("Sessão de líder expirada. Entre novamente.", 401)
    finally:
        c.close()


def _quando(r):
    return f"{DIAS_NOME[r['dia']]} {r['data'][8:]}/{r['data'][5:7]} às {r['hora']}"


@app.post("/api/whatsapp/marcar")
def whatsapp_marcar():
    """Registra como enviadas (manualmente, pela fila de 1 toque) as escalas informadas."""
    exigir_admin()
    d = request.get_json(silent=True) or {}
    ids = [i for i in (d.get("esc") or []) if cid(i)][:50] if isinstance(d.get("esc"), list) else []
    c = Conn()
    try:
        for i in ids:
            if c.execute("select 1 from escalas where id=?", (i,)).fetchone():
                c.execute("insert into envios values(?,?,?,?)", (i, time.time(), 1, "manual"))
    finally:
        c.close()
    return jsonify(ok=True)


def _tg(metodo, corpo=None):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/{metodo}", data=json.dumps(corpo or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def tg_chat():
    if TG_CHAT:
        return TG_CHAT
    c = Conn()
    try:
        r = c.execute("select v from config where k='tg_chat'").fetchone()
        return r["v"] if r else ""
    finally:
        c.close()


def tg_estado(c):
    if not TG_TOKEN:
        return "off"
    if TG_CHAT or c.execute("select 1 from config where k='tg_chat'").fetchone():
        return "ok"
    return "sem_chat"


def tg_avisar(evento, texto):
    """Avisa o líder no Telegram. Nunca derruba a requisição de quem está confirmando."""
    if not TG_TOKEN or evento not in TG_EVENTOS:
        return False
    try:
        chat = tg_chat()
        if not chat:
            return False
        _tg("sendMessage", {"chat_id": chat, "text": texto[:1000]})
        return True
    except Exception:
        return False


@app.post("/api/telegram/conectar")
def telegram_conectar():
    """Liga o bot ao Telegram do líder: ele envia /start ao bot e toca em 'Conectar'."""
    exigir_admin()
    if not TG_TOKEN:
        raise Erro("Falta o token do bot (variável TELEGRAM_BOT_TOKEN no servidor).")
    try:
        r = _tg("getUpdates")
    except Exception:
        raise Erro("Não consegui falar com o Telegram. Confira o token do bot.", 502)
    msgs = [u["message"] for u in r.get("result", []) if ((u.get("message") or {}).get("chat") or {}).get("type") == "private"]
    if not msgs:
        raise Erro("Nenhuma conversa encontrada. Abra o seu bot no Telegram, envie /start e tente de novo.")
    ch = msgs[-1]["chat"]
    c = Conn()
    try:
        c.execute("insert into config values('tg_chat',?) on conflict (k) do update set v=excluded.v", (str(ch["id"]),))
    finally:
        c.close()
    try:
        _tg("sendMessage", {"chat_id": ch["id"], "text": "✅ Conectado! Vou avisar aqui quando um voluntário aceitar, recusar ou pedir troca."})
    except Exception:
        pass
    return jsonify(nome=ch.get("first_name") or ch.get("username") or "você")


@app.post("/api/telegram/teste")
def telegram_teste():
    exigir_admin()
    if not TG_TOKEN or not tg_chat():
        raise Erro("O Telegram ainda não está conectado.")
    try:
        _tg("sendMessage", {"chat_id": tg_chat(), "text": "✅ Teste: os avisos das escalas estão funcionando."})
    except Exception:
        raise Erro("O Telegram recusou o envio. Reconecte o bot.", 502)
    return jsonify(ok=True)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return "ok" if PERSISTENTE else "ok (banco temporário)"


@app.get("/api/state")
def get_state():
    c = Conn()
    try:
        admin = e_admin(c)
        return jsonify(state=montar(c, admin), version=versao(c), admin=admin, persistente=PERSISTENTE, whatsapp=WA_OK and admin,
                       telegram=tg_estado(c) if admin else "off")
    finally:
        c.close()


@app.post("/api/state")
def post_state():
    d = request.get_json(silent=True) or {}
    with tx() as c:
        if not e_admin(c):
            raise Erro("Sessão de líder expirada. Entre novamente.", 401)
        if d.get("version") != versao(c):
            raise Erro("Os dados mudaram em outro lugar.", 409)
        aplicar(c, d.get("state"))
        return jsonify(version=versao(c))


@app.post("/api/admin/login")
def login():
    limitar("login", 10, 600)
    nome = norm((request.get_json(silent=True) or {}).get("nome"))
    with tx() as c:
        if not nome or nome not in {norm(r["nome"]) for r in c.execute("select nome from admins")}:
            raise Erro("Nome não consta na lista de líderes.", 403)
        c.execute("delete from sessoes where exp<?", (time.time(),))
        chave = secrets.token_urlsafe(24)
        c.execute("insert into sessoes values(?,?,?)", (chave, nome, time.time() + SESSAO_SEG))
    return jsonify(chave=chave)


@app.post("/api/vol")
def novo_voluntario():
    limitar("vol", 20, 3600)
    d = request.get_json(silent=True) or {}
    v = validar_voluntario(d)
    with tx() as c:
        if any(norm(r["nome"]) == norm(v["nome"]) for r in c.execute("select nome from voluntarios")):
            raise Erro("Já existe um voluntário com esse nome.", 409)
        i = uid()
        c.execute("insert into voluntarios values(?,?,?,?,?,?,?)", (i, v["nome"], v["zap"], v["mail"], v["obs"], v["funcs"], v["disp"]))
        bump(c)
    return jsonify(id=i)


@app.post("/api/aviso")
def novo_aviso():
    limitar("aviso", 20, 3600)
    d = request.get_json(silent=True) or {}
    msg = clean(d.get("msg"), 300) or "Pede troca"
    esc_id = cid(d.get("esc"))
    with tx() as c:
        v = c.execute("select nome from voluntarios where id=?", (d.get("vol"),)).fetchone()
        if not v:
            raise Erro("Voluntário não encontrado.", 404)
        e = c.execute("select e.ministerio, e.funcao, k.data, k.dia, k.hora from escalas e join cultos k on k.id=e.culto where e.id=?",
                      (esc_id,)).fetchone() if esc_id else None
        c.execute("insert into avisos values(?,?,?,?,?)", (uid(), d["vol"], esc_id, msg, int(time.time() * 1000)))
        bump(c)
    ctx = f" ({e['ministerio']} - {e['funcao']}, {_quando(e)})" if e else ""
    tg_avisar("troca", f"🔁 {v['nome']} pediu troca/avisou: {msg}{ctx}")
    return jsonify(ok=True)


def _escala_por_token(c, tok):
    r = c.execute("select e.*, v.nome vnome, k.data, k.dia, k.hora, k.tipo from escalas e "
                  "join voluntarios v on v.id=e.vol join cultos k on k.id=e.culto where e.tok=?", (tok,)).fetchone()
    if not r:
        raise Erro("Link inválido.", 404)
    return r


@app.get("/api/t/<tok>")
def ver_token(tok):
    limitar("tok", 60, 600)
    c = Conn()
    try:
        r = _escala_por_token(c, tok)
        return jsonify(escala={"id": r["id"], "min": r["ministerio"], "func": r["funcao"], "st": r["st"]},
                       culto={"data": r["data"], "dia": r["dia"], "hora": r["hora"], "tipo": r["tipo"]}, vol={"nome": r["vnome"]})
    finally:
        c.close()


@app.post("/api/t/<tok>")
def responder_token(tok):
    limitar("tok", 60, 600)
    st = (request.get_json(silent=True) or {}).get("st")
    if st not in ("aceito", "recusado"):
        raise Erro("Resposta inválida.")
    with tx() as c:
        r = _escala_por_token(c, tok)
        c.execute("update escalas set st=? where id=?", (st, r["id"]))
        if st == "recusado":
            c.execute("insert into avisos values(?,?,?,?,?)", (uid(), r["vol"], r["id"], "Recusou a escala", int(time.time() * 1000)))
        bump(c)
    tg_avisar(st, f"{'✅' if st == 'aceito' else '❌'} {r['vnome']} {'aceitou' if st == 'aceito' else 'recusou'}: "
                  f"{r['ministerio']} - {r['funcao']}, {_quando(r)}")
    return jsonify(st=st)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=bool(os.environ.get("DEBUG")))
