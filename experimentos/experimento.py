"""Experimento da Sprint 5 — compara Regras puras x PLN isolado x Híbrido.

Protocolo:
  * Validação cruzada estratificada 5-fold repetida 5x (seeds distintas) sobre data/dataset.csv.
    O classificador de PLN é treinado SÓ nas dobras de treino; as regras são fixas (léxico).
  * Cenário A (texto limpo) e Cenário B (texto com ruído de digitação nos textos de teste).
  * Métricas: acurácia, precisão/recall/F1 por setor, matriz de confusão, cobertura das regras,
    acurácia da urgência, latência de triagem (em processo) e latência HTTP sob concorrência.
Uso:  python -m experimentos.experimento
"""
import json
import os
import random
import tempfile
import threading
import time
import http.client
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import RepeatedStratifiedKFold

from app.triagem import SETORES, Triagem, carregar_dataset, classificar_urgencia, normalizar

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "evidencias"
SAIDA.mkdir(exist_ok=True)
MODOS = ["regras", "ml", "hibrido"]
ROTULOS_MODO = {"regras": "Regras puras", "ml": "PLN isolado", "hibrido": "Híbrido"}
LABELS = SETORES + ["indefinido"]


def adicionar_ruido(texto, rng, p=0.3):
    """Simula erros de digitação: troca de letras adjacentes, omissão ou duplicação de letra."""
    palavras = texto.split()
    for i, w in enumerate(palavras):
        if len(w) > 3 and rng.random() < p:
            k = rng.randrange(1, len(w) - 1)
            op = rng.choice(["troca", "omite", "dobra"])
            if op == "troca":
                w = w[:k] + w[k + 1] + w[k] + w[k + 2:]
            elif op == "omite":
                w = w[:k] + w[k + 1:]
            else:
                w = w[:k] + w[k] + w[k:]
            palavras[i] = w
    return " ".join(palavras)


def avaliar_cv():
    textos, setores, urgencias = carregar_dataset()
    X, y, urg = np.array(textos, dtype=object), np.array(setores), np.array(urgencias)
    rskf = RepeatedStratifiedKFold(n_splits=5, n_repeats=5, random_state=42)
    rng = random.Random(123)
    acc = {(c, m): [] for c in ("limpo", "ruido") for m in MODOS}
    pred = {(c, m): [] for c in ("limpo", "ruido") for m in MODOS}
    real = {c: [] for c in ("limpo", "ruido")}
    cobertura = {"limpo": [], "ruido": []}
    acc_urg = []
    for treino, teste in rskf.split(X, y):
        t = Triagem().treinar(list(X[treino]), list(y[treino]))
        for cen in ("limpo", "ruido"):
            textos_teste = [x if cen == "limpo" else adicionar_ruido(x, rng) for x in X[teste]]
            real[cen].extend(y[teste])
            res = {m: [t.triar(tx, m) for tx in textos_teste] for m in MODOS}
            for m in MODOS:
                p = [r["setor"] for r in res[m]]
                pred[(cen, m)].extend(p)
                acc[(cen, m)].append(accuracy_score(y[teste], p))
            cobertura[cen].append(np.mean([r["metodo"] == "regras" for r in res["hibrido"]]))
            if cen == "limpo":
                acc_urg.append(accuracy_score(urg[teste], [r["urgencia"] for r in res["hibrido"]]))
    return acc, pred, real, cobertura, acc_urg


def resumo_metricas(real, pred):
    p, r, f, s = precision_recall_fscore_support(real, pred, labels=SETORES, zero_division=0)
    return {SETORES[i]: {"precisao": round(float(p[i]), 3), "recall": round(float(r[i]), 3),
                         "f1": round(float(f[i]), 3), "suporte": int(s[i])} for i in range(3)}


def matriz(real, pred):
    return confusion_matrix(real, pred, labels=LABELS)


def latencia_processo():
    textos, _, _ = carregar_dataset()
    t = Triagem.do_dataset()
    out = {}
    for m in MODOS:
        for tx in textos[:10]:
            t.triar(tx, m)  # aquecimento
        tempos = []
        for _ in range(10):
            for tx in textos:
                t0 = time.perf_counter(); t.triar(tx, m); tempos.append((time.perf_counter() - t0) * 1000)
        out[m] = {"media_ms": round(float(np.mean(tempos)), 3), "p95_ms": round(float(np.percentile(tempos, 95)), 3),
                  "n": len(tempos)}
    return out


def carga_http():
    """Sobe o servidor real (werkzeug, threaded) e dispara POST /api/tickets com concorrência crescente."""
    from werkzeug.serving import make_server
    from app.app import create_app
    textos, _, _ = carregar_dataset()
    tmp = tempfile.TemporaryDirectory()
    app = create_app(db_path=os.path.join(tmp.name, "carga.db"))
    srv = make_server("127.0.0.1", 5055, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)

    def chamar(metodo, caminho, corpo=None, token=None):
        c = http.client.HTTPConnection("127.0.0.1", 5055, timeout=30)
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        t0 = time.perf_counter()
        c.request(metodo, caminho, json.dumps(corpo) if corpo else None, h)
        r = c.getresponse(); dados = r.read(); c.close()
        return r.status, (time.perf_counter() - t0) * 1000, dados

    _, _, d = chamar("POST", "/api/auth/login", {"email": "aluno@unama.test", "senha": "aluno123"})
    token = json.loads(d)["token"]
    resultados = {}
    for conc, total in ((1, 100), (10, 200), (50, 300)):
        def tarefa(i):
            tx = textos[i % len(textos)]
            st, ms, _ = chamar("POST", "/api/tickets", {"titulo": tx[:60], "descricao": tx}, token)
            return st, ms
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=conc) as ex:
            saidas = list(ex.map(tarefa, range(total)))
        dur = time.perf_counter() - t0
        lat = [ms for st, ms in saidas if st == 201]
        resultados[conc] = {"requisicoes": total, "sucesso": len(lat), "media_ms": round(float(np.mean(lat)), 1),
                            "p50_ms": round(float(np.percentile(lat, 50)), 1), "p95_ms": round(float(np.percentile(lat, 95)), 1),
                            "req_por_s": round(total / dur, 1)}
    srv.shutdown(); tmp.cleanup()
    return resultados


def graficos(acc, real, pred, lat_http):
    # 1) acurácia por método e cenário
    fig, ax = plt.subplots(figsize=(7, 4))
    larg = 0.35
    for j, (cen, nome) in enumerate((("limpo", "Texto limpo"), ("ruido", "Com erros de digitação"))):
        medias = [np.mean(acc[(cen, m)]) * 100 for m in MODOS]
        desv = [np.std(acc[(cen, m)]) * 100 for m in MODOS]
        b = ax.bar(np.arange(3) + j * larg, medias, larg, yerr=desv, capsize=3, label=nome)
        for rect, v in zip(b, medias):
            ax.text(rect.get_x() + rect.get_width() / 2, v + 2, f"{v:.1f}", ha="center", fontsize=8)
    ax.axhline(85, color="red", ls="--", lw=1, label="Meta ≥ 85%")
    ax.set_xticks(np.arange(3) + larg / 2); ax.set_xticklabels([ROTULOS_MODO[m] for m in MODOS])
    ax.set_ylabel("Acurácia (%)"); ax.set_ylim(0, 110); ax.set_title("Acurácia no direcionamento de setor (CV 5x5)")
    ax.legend(fontsize=8, loc="lower right"); fig.tight_layout(); fig.savefig(SAIDA / "fig_acuracia.png", dpi=150); plt.close(fig)
    # 2) matrizes de confusão do híbrido
    fig, axs = plt.subplots(1, 2, figsize=(9, 3.8))
    for ax, cen, nome in zip(axs, ("limpo", "ruido"), ("Texto limpo", "Com ruído")):
        cm = matriz(real[cen], pred[(cen, "hibrido")]); cm = cm / 5  # média por repetição
        ax.imshow(cm, cmap="Blues")
        ax.set_xticks(range(4)); ax.set_xticklabels(["Fin.", "Sec.", "Coord.", "Indef."], fontsize=8)
        ax.set_yticks(range(3)); ax.set_yticklabels(["Fin.", "Sec.", "Coord."], fontsize=8)
        ax.set_ylim(2.5, -0.5)
        for i in range(3):
            for k in range(4):
                ax.text(k, i, f"{cm[i, k]:.1f}", ha="center", va="center", fontsize=9, color="black" if cm[i, k] < cm.max() / 2 else "white")
        ax.set_title(f"Híbrido — {nome}", fontsize=10); ax.set_xlabel("Previsto"); ax.set_ylabel("Real")
    fig.tight_layout(); fig.savefig(SAIDA / "fig_matriz_confusao.png", dpi=150); plt.close(fig)
    # 3) latência HTTP
    fig, ax = plt.subplots(figsize=(6, 3.6))
    cs = list(lat_http.keys()); x = np.arange(len(cs))
    ax.bar(x - 0.2, [lat_http[c]["p50_ms"] for c in cs], 0.4, label="p50")
    ax.bar(x + 0.2, [lat_http[c]["p95_ms"] for c in cs], 0.4, label="p95")
    ax.set_xticks(x); ax.set_xticklabels([f"{c} simult." for c in cs]); ax.set_ylabel("ms")
    ax.set_title("Latência HTTP de POST /api/tickets (triagem + gravação)"); ax.legend(); fig.tight_layout()
    fig.savefig(SAIDA / "fig_latencia_http.png", dpi=150); plt.close(fig)


def main():
    print("Executando validação cruzada (5x5)...")
    acc, pred, real, cobertura, acc_urg = avaliar_cv()
    out = {"dataset": {"n": len(real["limpo"]) // 5, "setores": SETORES}, "acuracia": {}, "metricas_por_setor_hibrido": {},
           "cobertura_regras_no_hibrido": {c: round(float(np.mean(v)) * 100, 1) for c, v in cobertura.items()},
           "acuracia_urgencia_regras": {"media": round(float(np.mean(acc_urg)) * 100, 1), "desvio": round(float(np.std(acc_urg)) * 100, 1)}}
    for cen in ("limpo", "ruido"):
        out["acuracia"][cen] = {m: {"media": round(float(np.mean(acc[(cen, m)])) * 100, 1),
                                    "desvio": round(float(np.std(acc[(cen, m)])) * 100, 1),
                                    "global_agregada": round(accuracy_score(real[cen], pred[(cen, m)]) * 100, 1)} for m in MODOS}
        out["metricas_por_setor_hibrido"][cen] = resumo_metricas(real[cen], pred[(cen, "hibrido")])
    out["matriz_confusao_hibrido_soma_5_repeticoes"] = {c: matriz(real[c], pred[(c, "hibrido")]).tolist() for c in ("limpo", "ruido")}
    print("Medindo latência em processo...")
    out["latencia_processo"] = latencia_processo()
    print("Executando teste de carga HTTP...")
    out["latencia_http"] = carga_http()
    graficos(acc, real, pred, out["latencia_http"])
    (SAIDA / "resultados_experimento.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
