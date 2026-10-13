import io, json, os, sys, tempfile, unittest, urllib.error
from unittest import mock
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["ADMINS"] = "Pastor Teste, Líder Dois"
os.environ.update(WHATSAPP_TOKEN="tok123", WHATSAPP_PHONE_ID="999", PUBLIC_URL="https://escalas.exemplo.org", TELEGRAM_BOT_TOKEN="123:abc")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = A.app.test_client()
        cls._rede = mock.patch("urllib.request.urlopen", side_effect=OSError("sem rede nos testes"))
        cls._rede.start()

    @classmethod
    def tearDownClass(cls):
        cls._rede.stop()

    def login(self, nome="pastor  TESTE"):
        r = self.c.post("/api/admin/login", json={"nome": nome})
        return r, {"X-Admin-Key": r.get_json().get("chave", "")}

    def test_fluxo_completo(self):
        c = self.c
        # login: aceita acento/caixa/espaços; recusa desconhecido
        self.assertEqual(c.post("/api/admin/login", json={"nome": "Fulano"}).status_code, 403)
        r, h = self.login("lider DOIS")
        self.assertEqual(r.status_code, 200)
        # público não vê admins, telefones nem tokens
        r = c.post("/api/vol", json={"nome": "Maria Silva", "zap": "65999", "funcs": ["Câmeras", "Fotografia", "Inválida"], "disp": [6, 0, 2]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.post("/api/vol", json={"nome": "maria  silva", "funcs": ["Câmeras"], "disp": [6]}).status_code, 409)
        pub = c.get("/api/state").get_json()
        self.assertNotIn("admins", pub["state"]); self.assertNotIn("zap", pub["state"]["vols"][0])
        # líder monta culto de sábado + domingo e escala
        adm = c.get("/api/state", headers=h).get_json()
        s, ver = adm["state"], adm["version"]
        self.assertEqual(s["vols"][0]["zap"], "65999"); self.assertEqual(s["vols"][0]["funcs"], ["Câmeras", "Fotografia"])
        vid = s["vols"][0]["id"]
        s["cultos"] = [{"id": "c1", "data": "2026-10-10", "hora": "19:00", "tipo": "Culto de Sábado"},
                       {"id": "c2", "data": "2026-10-11", "hora": "19:00", "tipo": "Culto de Domingo"}]
        s["esc"] = [{"id": "e1", "culto": "c1", "vol": vid, "min": "Transmissão", "func": "Câmeras", "st": "pendente", "tok": "a" * 24}]
        r = c.post("/api/state", json={"version": ver, "state": s}, headers=h)
        self.assertEqual(r.status_code, 200, r.get_json())
        ver = r.get_json()["version"]
        # versão velha -> 409; sem chave -> 401
        self.assertEqual(c.post("/api/state", json={"version": ver - 1, "state": s}, headers=h).status_code, 409)
        self.assertEqual(c.post("/api/state", json={"version": ver, "state": s}).status_code, 401)
        # regras: Transmissão só no sábado; conflito no mesmo horário
        s2 = c.get("/api/state", headers=h).get_json()["state"]
        s2["esc"].append({"id": "e2", "culto": "c2", "vol": vid, "min": "Transmissão", "func": "Câmeras", "st": "pendente", "tok": "b" * 24})
        self.assertEqual(c.post("/api/state", json={"version": ver, "state": s2}, headers=h).status_code, 422)
        s2["esc"][-1]["min"] = "Mídia"; s2["esc"][-1]["func"] = "Fotografia"; s2["esc"][-1]["culto"] = "c1"
        self.assertEqual(c.post("/api/state", json={"version": ver, "state": s2}, headers=h).status_code, 422)
        # token: ver e responder; público não enxerga o token na lista
        pub = c.get("/api/state").get_json()["state"]
        self.assertNotIn("tok", pub["esc"][0])
        t = c.get("/api/t/" + "a" * 24).get_json()
        self.assertEqual(t["vol"]["nome"], "Maria Silva")
        self.assertEqual(c.get("/api/t/" + "z" * 24).status_code, 404)
        self.assertEqual(c.post("/api/t/" + "a" * 24, json={"st": "faltou"}).status_code, 400)
        self.assertEqual(c.post("/api/t/" + "a" * 24, json={"st": "recusado"}).status_code, 200)
        adm = c.get("/api/state", headers=h).get_json()["state"]
        self.assertEqual(adm["esc"][0]["st"], "recusado"); self.assertEqual(adm["av"][0]["msg"], "Recusou a escala")
        # aviso de troca
        self.assertEqual(c.post("/api/aviso", json={"vol": vid, "esc": "e1", "msg": "Preciso trocar"}).status_code, 200)
        # não pode remover todos os líderes
        adm["admins"] = []
        v = c.get("/api/state", headers=h).get_json()["version"]
        self.assertEqual(c.post("/api/state", json={"version": v, "state": adm}, headers=h).status_code, 422)

    def test_telegram_e_fila(self):
        c = self.c
        _, h = self.login("pastor teste")
        self.assertEqual(c.get("/api/state", headers=h).get_json()["telegram"], "sem_chat")
        self.assertEqual(c.get("/api/state").get_json()["telegram"], "off")
        calls, updates = [], []

        class R:
            def __init__(self, b): self.b = b
            def read(self): return json.dumps(self.b).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def tg(req, timeout=None):
            body = json.loads(req.data) if req.data else {}
            calls.append((req.full_url, body))
            return R({"ok": True, "result": updates if "getUpdates" in req.full_url else {}})

        with mock.patch("urllib.request.urlopen", tg):
            self.assertEqual(c.post("/api/telegram/conectar").status_code, 401)      # só líder
            r = c.post("/api/telegram/conectar", headers=h)                          # ninguém deu /start ainda
            self.assertEqual(r.status_code, 400); self.assertIn("/start", r.get_json()["erro"])
            updates.append({"update_id": 1, "message": {"chat": {"id": 4242, "type": "private", "first_name": "Pastor"}, "text": "/start"}})
            r = c.post("/api/telegram/conectar", headers=h)
            self.assertEqual(r.status_code, 200); self.assertEqual(r.get_json()["nome"], "Pastor")
            self.assertEqual(c.get("/api/state", headers=h).get_json()["telegram"], "ok")
            self.assertEqual(c.post("/api/telegram/teste", headers=h).status_code, 200)
            self.assertEqual(str(calls[-1][1]["chat_id"]), "4242")
            self.assertIn("Teste", calls[-1][1]["text"])
        # escala + token: aceite e recusa avisam o líder; falha do Telegram não quebra a confirmação
        adm = c.get("/api/state", headers=h).get_json(); s, ver = adm["state"], adm["version"]
        s["vols"].append({"id": "tg1", "nome": "Joana Tg", "zap": "65988887777", "mail": "", "obs": "", "funcs": ["Câmeras"], "disp": [6]})
        s["cultos"].append({"id": "c7", "data": "2026-10-24", "hora": "19:00", "tipo": "Culto de Sábado"})
        s["esc"].append({"id": "tg1e", "culto": "c7", "vol": "tg1", "min": "Transmissão", "func": "Câmeras", "st": "pendente", "tok": "e" * 24})
        self.assertEqual(c.post("/api/state", json={"version": ver, "state": s}, headers=h).status_code, 200)
        calls.clear()
        with mock.patch("urllib.request.urlopen", tg):
            self.assertEqual(c.post("/api/t/" + "e" * 24, json={"st": "aceito"}).status_code, 200)
            txt = calls[-1][1]["text"]; self.assertEqual(calls[-1][1]["chat_id"], "4242")
            self.assertIn("✅ Joana Tg aceitou: Transmissão - Câmeras, Sábado 24/10 às 19:00", txt)
            self.assertEqual(c.post("/api/t/" + "e" * 24, json={"st": "recusado"}).status_code, 200)
            self.assertIn("❌ Joana Tg recusou", calls[-1][1]["text"])
            self.assertEqual(c.post("/api/aviso", json={"vol": "tg1", "esc": "tg1e", "msg": "Viajo nesse dia"}).status_code, 200)
            self.assertIn("🔁 Joana Tg pediu troca/avisou: Viajo nesse dia (Transmissão - Câmeras", calls[-1][1]["text"])
        # (rede indisponível: o fixture global lança OSError) a confirmação continua funcionando
        self.assertEqual(c.post("/api/t/" + "e" * 24, json={"st": "aceito"}).status_code, 200)
        # fila de 1 toque: marcar como enviado (manual) só para líder e só escalas que existem
        self.assertEqual(c.post("/api/whatsapp/marcar", json={"esc": ["tg1e"]}).status_code, 401)
        self.assertEqual(c.post("/api/whatsapp/marcar", json={"esc": ["tg1e", "naoexiste"]}, headers=h).status_code, 200)
        env = c.get("/api/state", headers=h).get_json()["state"]["envios"]
        self.assertTrue(env["tg1e"]["ok"]); self.assertEqual(env["tg1e"]["msg"], "manual"); self.assertNotIn("naoexiste", env)

    def test_whatsapp(self):
        c = self.c
        _, h = self.login("pastor teste")
        adm = c.get("/api/state", headers=h).get_json()
        self.assertTrue(adm["whatsapp"]); self.assertFalse(c.get("/api/state").get_json()["whatsapp"])
        s, ver = adm["state"], adm["version"]
        s["vols"] += [{"id": "wa1", "nome": "Com Zap", "zap": "(65) 99999-0001", "mail": "", "obs": "", "funcs": ["Câmeras"], "disp": [6]},
                      {"id": "wa2", "nome": "Sem Zap", "zap": "", "mail": "", "obs": "", "funcs": ["Câmeras"], "disp": [6]}]
        s["cultos"].append({"id": "c9", "data": "2026-10-17", "hora": "19:00", "tipo": "Culto de Sábado"})
        s["esc"] += [{"id": "w1", "culto": "c9", "vol": "wa1", "min": "Transmissão", "func": "Câmeras", "st": "pendente", "tok": "c" * 24},
                     {"id": "w2", "culto": "c9", "vol": "wa2", "min": "Mídia", "func": "Fotografia", "st": "pendente", "tok": "d" * 24}]
        self.assertEqual(c.post("/api/state", json={"version": ver, "state": s}, headers=h).status_code, 200)
        calls = []

        class R:
            def __init__(self, b): self.b = b
            def read(self): return json.dumps(self.b).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def ok(req, timeout=None):
            calls.append((req.full_url, dict(req.header_items()), json.loads(req.data)))
            return R({"messages": [{"id": "wamid.X"}]})

        # sem sessão de líder: 401 e nada é enviado
        with mock.patch("urllib.request.urlopen", ok):
            self.assertEqual(c.post("/api/whatsapp/enviar", json={"esc": ["w1"]}).status_code, 401)
            r = c.post("/api/whatsapp/enviar", json={"esc": ["w1", "w2", "naoexiste"]}, headers=h)
        self.assertEqual(r.status_code, 200)
        res = {x["id"]: x for x in r.get_json()["resultados"]}
        self.assertTrue(res["w1"]["ok"]); self.assertIn("sem WhatsApp", res["w2"]["erro"]); self.assertFalse(res["naoexiste"]["ok"])
        self.assertEqual(len(calls), 1)
        url, hd, body = calls[0]
        self.assertEqual(url, "https://graph.facebook.com/v23.0/999/messages")
        self.assertEqual(hd["Authorization"], "Bearer tok123")
        self.assertEqual(body["to"], "5565999990001"); self.assertEqual(body["type"], "template")
        self.assertEqual(body["template"]["name"], "escala_voluntario"); self.assertEqual(body["template"]["language"]["code"], "pt_BR")
        ps = [p["text"] for p in body["template"]["components"][0]["parameters"]]
        self.assertEqual(ps[:2], ["Com Zap", "Transmissão - Câmeras"]); self.assertIn("Sábado 17/10 às 19:00", ps[2])
        self.assertEqual(ps[3], "https://escalas.exemplo.org/#t=" + "c" * 24)
        # status por escala aparece só para o líder; falha da Meta é registrada
        err = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(json.dumps({"error": {"message": "Template not approved"}}).encode()))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            r = c.post("/api/whatsapp/enviar", json={"esc": ["w1"]}, headers=h)
        self.assertIn("Template not approved", r.get_json()["resultados"][0]["erro"])
        env = c.get("/api/state", headers=h).get_json()["state"]["envios"]
        self.assertFalse(env["w1"]["ok"]); self.assertNotIn("envios", c.get("/api/state").get_json()["state"])

    def test_estatico(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200); self.assertIn(b"Escalas", r.data)
        self.assertEqual(self.c.get("/healthz").data, b"ok")


if __name__ == "__main__":
    unittest.main()
