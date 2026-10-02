"""Baseline MANUAL (linha de base do experimento).

1) `python -m experimentos.baseline_manual preparar`
   Gera data/amostra_manual.csv (30 chamados embaralhados, sem rótulo) e
   data/gabarito_manual.csv (uso do pesquisador). Entregue a amostra a 2-3 voluntários
   (colegas/atendentes) e peça que classifiquem cada chamado em Financeiro, Secretaria ou
   Coordenação, cronometrando o tempo gasto em cada um (em segundos).
2) Preencha as colunas `setor_escolhido` e `segundos` (um arquivo por avaliador, ex.:
   data/manual_avaliador1.csv, mesmo formato da amostra + coluna `avaliador`).
3) `python -m experimentos.baseline_manual comparar`
   Calcula acurácia e tempo médio manuais e compara com o algoritmo híbrido nos MESMOS chamados.
"""
import csv, glob, sys, time
from pathlib import Path
import numpy as np
from app.triagem import Triagem, carregar_dataset

D = Path(__file__).resolve().parent.parent / "data"


def preparar():
    import random
    textos, setores, _ = carregar_dataset()
    rng = random.Random(7)
    por_setor = {}
    for i, s in enumerate(setores):
        por_setor.setdefault(s, []).append(i)
    escolhidos = [i for s in por_setor for i in rng.sample(por_setor[s], 10)]
    rng.shuffle(escolhidos)
    with open(D / "amostra_manual.csv", "w", newline="", encoding="utf-8") as f, \
         open(D / "gabarito_manual.csv", "w", newline="", encoding="utf-8") as g:
        w, wg = csv.writer(f), csv.writer(g)
        w.writerow(["chamado", "texto", "avaliador", "setor_escolhido", "segundos"])
        wg.writerow(["chamado", "setor_correto"])
        for n, i in enumerate(escolhidos, 1):
            w.writerow([n, textos[i], "", "", ""]); wg.writerow([n, setores[i]])
    print("Gerados data/amostra_manual.csv e data/gabarito_manual.csv (30 chamados).")


def comparar():
    arquivos = sorted(glob.glob(str(D / "manual_avaliador*.csv")))
    if not arquivos:
        print("Nenhum arquivo data/manual_avaliador*.csv encontrado — execute o protocolo manual primeiro."); return
    gab = {r["chamado"]: r["setor_correto"] for r in csv.DictReader(open(D / "gabarito_manual.csv", encoding="utf-8"))}
    acertos, tempos, textos = [], [], {}
    for a in arquivos:
        for r in csv.DictReader(open(a, encoding="utf-8")):
            if not r["setor_escolhido"] or not r["segundos"]:
                continue
            textos[r["chamado"]] = r["texto"]
            acertos.append(r["setor_escolhido"].strip() == gab[r["chamado"]]); tempos.append(float(r["segundos"]))
    t = Triagem.do_dataset()  # atenção: treinado com todo o dataset (inclui a amostra) — use CV para avaliação justa
    ms = []
    hit = []
    for c, tx in textos.items():
        t0 = time.perf_counter(); r = t.triar(tx); ms.append((time.perf_counter() - t0) * 1000); hit.append(r["setor"] == gab[c])
    print(f"Manual : acurácia {np.mean(acertos)*100:.1f}% | tempo médio {np.mean(tempos):.1f} s/chamado (n={len(tempos)} classificações)")
    print(f"Híbrido: acurácia {np.mean(hit)*100:.1f}% | tempo médio {np.mean(ms):.3f} ms/chamado")
    print(f"Redução de tempo: {np.mean(tempos)*1000/np.mean(ms):.0f}x")


if __name__ == "__main__":
    {"preparar": preparar, "comparar": comparar}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
