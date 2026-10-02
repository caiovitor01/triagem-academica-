# Triagem Híbrida de Chamados Acadêmicos — MVP Alpha

Sistema web de chamados em que o aluno descreve o problema e uma API de **triagem híbrida**
(regras + PLN leve) define automaticamente **setor** (Financeiro, Secretaria, Coordenação) e
**urgência**, enviando o chamado à fila correta. O atendente responde pelo painel.

## Executar
```bash
pip install -r requirements.txt
python -m app.app            # http://localhost:5000
```
Contas de demonstração: `aluno@unama.test / aluno123`; atendentes `financeiro@`, `secretaria@`,
`coordenacao@`, `supervisor@unama.test / atende123` (o supervisor enxerga todos os setores).

## Testes e experimento (evidências)
```bash
python -m unittest -v tests.test_api            # 8 testes de integração
python -m experimentos.experimento               # CV 5x5, ruído, latência, carga HTTP -> evidencias/
python -m experimentos.capturas                  # prints da interface -> evidencias/
python -m experimentos.baseline_manual preparar  # protocolo do baseline manual (ver docstring)
```

## Endpoints principais
`POST /api/auth/register|login` · `GET /api/me` · `POST /api/tickets` (aluno) · `GET /api/tickets` ·
`GET /api/tickets/<id>` · `POST /api/tickets/<id>/respostas` · `PATCH /api/tickets/<id>` (status/setor, atendente) ·
`GET /api/estatisticas` · `POST /api/triagem` (simulação, modos: hibrido|regras|ml)

## Estrutura
`app/triagem.py` motor híbrido · `app/app.py` API + JWT + SQLite · `app/static/index.html` interface ·
`data/dataset.csv` dataset sintético rotulado (135 chamados) · `experimentos/` scripts · `evidencias/` resultados.

## Observações
* Implementado em Python/Flask + SQLite (verificável sem instalar Maven/PostgreSQL). A arquitetura é
  equivalente à planejada (API REST + JWT + banco relacional + container) e pode ser portada para Spring Boot/PostgreSQL.
* Em produção defina `SECRET_KEY` e troque as contas de demonstração.
