"""Gera prints da interface (evidências do MVP Alpha) usando Playwright + servidor real."""
import os, tempfile, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from app.app import create_app

SAIDA = Path(__file__).resolve().parent.parent / "evidencias"
tmp = tempfile.TemporaryDirectory()
srv = make_server("127.0.0.1", 5056, create_app(db_path=os.path.join(tmp.name, "c.db")), threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.5)
URL = "http://127.0.0.1:5056/"

CHAMADOS = [
  ("Boleto vencido", "O boleto da mensalidade venceu ontem e o sistema não deixa emitir outro, preciso pagar hoje"),
  ("Histórico escolar", "Como faço para solicitar meu histórico escolar para entregar no estágio"),
  ("Revisão de nota", "Gostaria de pedir revisão da nota da prova de banco de dados, o professor não respondeu"),
]
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1100, "height": 800})
    pg.goto(URL); pg.screenshot(path=str(SAIDA / "tela_01_login.png"))
    pg.click("button:has-text('Entrar')")
    pg.wait_for_selector("#telaAluno:not(.hidden)")
    for t, d in CHAMADOS:
        pg.fill("#tTitulo", t); pg.fill("#tDesc", d); pg.click("button:has-text('Enviar chamado')")
        pg.wait_for_selector("#tOk .ok"); time.sleep(0.3)
    pg.screenshot(path=str(SAIDA / "tela_02_aluno_chamado_triado.png"), full_page=True)
    pg.click("button:has-text('Sair')")
    pg.fill("#lEmail", "supervisor@unama.test"); pg.fill("#lSenha", "atende123"); pg.click("button:has-text('Entrar')")
    pg.wait_for_selector("#telaAtendente:not(.hidden)"); time.sleep(0.5)
    pg.screenshot(path=str(SAIDA / "tela_03_painel_atendente.png"), full_page=True)
    pg.click("#fila .item >> nth=0"); pg.wait_for_selector("#telaDetalhe:not(.hidden)")
    pg.fill("#nMsg", "Olá! Enviamos a segunda via do boleto para o seu e-mail."); pg.click("#detalhe button:has-text('Enviar')"); time.sleep(0.6)
    pg.screenshot(path=str(SAIDA / "tela_04_atendente_responde.png"), full_page=True)
    b.close()
srv.shutdown(); tmp.cleanup(); print("ok")
