"""Testes de integração do MVP Alpha (fluxo completo: login -> chamado -> triagem -> painel)."""
import os
import tempfile
import unittest

from app.app import create_app


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(db_path=os.path.join(cls.tmp.name, "t.db"))
        cls.c = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def login(self, email, senha):
        r = self.c.post("/api/auth/login", json={"email": email, "senha": senha})
        self.assertEqual(r.status_code, 200, r.get_json())
        return {"Authorization": "Bearer " + r.get_json()["token"]}

    def test_01_login_invalido(self):
        r = self.c.post("/api/auth/login", json={"email": "aluno@unama.test", "senha": "errada"})
        self.assertEqual(r.status_code, 401)

    def test_02_rotas_exigem_token(self):
        self.assertEqual(self.c.get("/api/tickets").status_code, 401)
        self.assertEqual(self.c.get("/api/tickets", headers={"Authorization": "Bearer xxx"}).status_code, 401)

    def test_03_cadastro_e_duplicidade(self):
        dados = {"nome": "Maria", "email": "maria@teste.com", "senha": "segredo1"}
        self.assertEqual(self.c.post("/api/auth/register", json=dados).status_code, 201)
        self.assertEqual(self.c.post("/api/auth/register", json=dados).status_code, 409)
        self.assertEqual(self.c.post("/api/auth/register", json={"nome": "x", "email": "ruim", "senha": "1"}).status_code, 400)

    def test_04_fluxo_completo_triagem_e_painel(self):
        aluno = self.login("aluno@unama.test", "aluno123")
        casos = [
            ("Segunda via de boleto", "Preciso da segunda via do boleto da mensalidade, venceu ontem e preciso pagar hoje", "Financeiro", "alta"),
            ("Trancamento", "Quero trancar a matrícula neste semestre por motivos pessoais", "Secretaria", None),
            ("Revisão de nota", "Gostaria de pedir revisão da nota da prova de banco de dados com o professor", "Coordenação", None),
        ]
        ids = {}
        for titulo, desc, setor, urg in casos:
            r = self.c.post("/api/tickets", json={"titulo": titulo, "descricao": desc}, headers=aluno)
            self.assertEqual(r.status_code, 201, r.get_json())
            t = r.get_json()
            self.assertEqual(t["setor"], setor)
            if urg:
                self.assertEqual(t["urgencia"], urg)
            self.assertLess(t["tempo_triagem_ms"], 500)
            ids[setor] = t["id"]

        fin = self.login("financeiro@unama.test", "atende123")
        lista = self.c.get("/api/tickets", headers=fin).get_json()
        self.assertTrue(all(t["setor"] == "Financeiro" for t in lista))
        self.assertIn(ids["Financeiro"], [t["id"] for t in lista])
        # atendente do financeiro não vê chamado de outro setor
        self.assertEqual(self.c.get(f"/api/tickets/{ids['Secretaria']}", headers=fin).status_code, 404)

        r = self.c.post(f"/api/tickets/{ids['Financeiro']}/respostas", json={"mensagem": "Segunda via enviada por e-mail."}, headers=fin)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["status"], "em_andamento")
        r = self.c.patch(f"/api/tickets/{ids['Financeiro']}", json={"status": "resolvido"}, headers=fin)
        self.assertEqual(r.get_json()["status"], "resolvido")

        sup = self.login("supervisor@unama.test", "atende123")
        self.assertGreaterEqual(len(self.c.get("/api/tickets", headers=sup).get_json()), 3)
        est = self.c.get("/api/estatisticas", headers=sup).get_json()
        self.assertGreaterEqual(est["total"], 3)

    def test_05_reencaminhamento(self):
        aluno = self.login("aluno@unama.test", "aluno123")
        t = self.c.post("/api/tickets", json={"titulo": "Duvida geral", "descricao": "Preciso de uma declaração de vínculo para o estágio"}, headers=aluno).get_json()
        sup = self.login("supervisor@unama.test", "atende123")
        r = self.c.patch(f"/api/tickets/{t['id']}", json={"setor": "Coordenação"}, headers=sup).get_json()
        self.assertEqual(r["setor"], "Coordenação")
        self.assertEqual(r["setor_original"], t["setor"])

    def test_06_perfis_e_isolamento(self):
        aluno = self.login("aluno@unama.test", "aluno123")
        outro = self.c.post("/api/auth/register", json={"nome": "Joao", "email": "joao@teste.com", "senha": "senha123"}).get_json()
        h2 = {"Authorization": "Bearer " + outro["token"]}
        t = self.c.post("/api/tickets", json={"titulo": "Privado", "descricao": "Preciso do histórico escolar completo"}, headers=aluno).get_json()
        self.assertEqual(self.c.get(f"/api/tickets/{t['id']}", headers=h2).status_code, 404)  # aluno não vê chamado alheio
        self.assertEqual(self.c.patch(f"/api/tickets/{t['id']}", json={"status": "resolvido"}, headers=aluno).status_code, 403)
        fin = self.login("financeiro@unama.test", "atende123")
        self.assertEqual(self.c.post("/api/tickets", json={"titulo": "abc", "descricao": "texto longo o bastante"}, headers=fin).status_code, 403)

    def test_07_validacoes(self):
        aluno = self.login("aluno@unama.test", "aluno123")
        self.assertEqual(self.c.post("/api/tickets", json={"titulo": "", "descricao": "curto"}, headers=aluno).status_code, 400)
        r = self.c.post("/api/triagem", json={"texto": "bolto da mensalidde vence amanha", "modo": "hibrido"}, headers=aluno)
        self.assertEqual(r.get_json()["setor"], "Financeiro")  # tolera erros de digitação via PLN

    def test_08_frontend_e_health(self):
        self.assertEqual(self.c.get("/api/health").get_json(), {"status": "ok"})
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Triagem", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
