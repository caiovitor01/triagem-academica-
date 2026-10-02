"""Motor de triagem híbrida de chamados acadêmicos.

Fluxo (conforme a metodologia da Sprint 2/3):
  1. Normaliza o texto (minúsculas, sem acentos/pontuação).
  2. Motor de regras: léxico ponderado por setor. Se um setor vence com margem
     suficiente, a decisão é tomada pelas regras (custo ~0, previsível).
  3. Caso contrário (incerteza), aciona o classificador de PLN leve
     (TF-IDF de palavras + n-gramas de caracteres + Regressão Logística),
     que tolera erros de digitação.
  4. Se a confiança do classificador for baixa, o chamado vai para o setor
     previsto mas é marcado com `revisao_manual=True`.
Urgência (alta/media/baixa) é definida por léxico de regras.
"""
import csv
import re
import time
import unicodedata
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

SETORES = ["Financeiro", "Secretaria", "Coordenação"]
DATASET_PADRAO = Path(__file__).resolve().parent.parent / "data" / "dataset.csv"


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9\s-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- léxico de regras
# (regex sobre texto normalizado, peso). Peso 2 = termo forte; 1 = termo fraco.
LEXICO = {
    "Financeiro": [
        (r"boleto", 2), (r"mensalidade", 2), (r"parcela", 2), (r"cobranc|cobrad", 2),
        (r"estorno", 2), (r"reembolso", 2), (r"\bfies\b|prouni", 2), (r"inadimpl", 2),
        (r"negativad", 2), (r"financeir|financiamento", 2), (r"\bpix\b", 2),
        (r"quitac|quitad|quitei", 2), (r"nota fiscal", 2), (r"juros", 2),
        (r"desconto", 2), (r"debito", 2), (r"bolsa", 2),
        (r"pag(amento|uei|ar|o)\b", 1), (r"\bvalor", 1), (r"\btaxa", 1),
        (r"cartao", 1), (r"multa", 1), (r"vencimento", 1), (r"acordo", 1), (r"devoluc", 1),
    ],
    "Secretaria": [
        (r"historico", 2), (r"declaracao", 2), (r"tranc(amento|ar|ad)", 2), (r"diploma", 2),
        (r"colacao", 2), (r"carteirinha", 2), (r"transfer(encia|ir)", 2),
        (r"(atestado|comprovante) de matricula", 2), (r"secretaria", 2),
        (r"certificado de conclusao", 2), (r"calendario academico", 2), (r"cadastr", 2),
        (r"documentac|documentos", 2), (r"rematricula", 2),
        (r"matricula", 1), (r"certificado", 1), (r"vinculo", 1), (r"segunda via", 1),
        (r"cancelar", 1), (r"reativ", 1), (r"reabertura", 1),
    ],
    "Coordenação": [
        (r"disciplina", 2), (r"\btcc\b", 2), (r"orientador|orientacao", 2), (r"estagio", 2),
        (r"horas complementares", 2), (r"aproveitamento", 2), (r"dependencia", 2),
        (r"\bgrade\b", 2), (r"optativa", 2), (r"coordenador|coordenacao", 2),
        (r"monitor", 2), (r"ementa", 2), (r"plano de ensino", 2), (r"banca", 2),
        (r"segunda chamada", 2), (r"frequencia", 2), (r"extensao", 2),
        (r"iniciacao cientifica", 2), (r"pre-?requisito", 2), (r"professor", 2), (r"prova", 2),
        (r"\bnotas?\b", 1), (r"turma", 1), (r"\bcurso\b", 1), (r"avaliac", 1), (r"trabalho", 1),
    ],
}
_LEXICO_COMPILADO = {s: [(re.compile(p), w) for p, w in termos] for s, termos in LEXICO.items()}

_URG_ALTA = re.compile(
    r"urgent|urgencia|imediat|ainda hoje|\bhoje\b|amanha|venceu|fecha amanha|prazo (termina|encerra|final)|"
    r"encerra|bloquead|engano|perco|duplicidade|negativad|nao consigo")
_URG_MEDIA = re.compile(
    r"preciso|solicit|erro|errad|problema|pendente|pendenc|nao (consigo|foi|recebi|aceit|lanc)|"
    r"reprov|conflito|cobrad|cobranc|acordo|atrasad|atraso|reclamar|falta")
_URG_BAIXA = re.compile(r"\bcomo\b|\bqual\b|\bquais\b|\bquando\b|\bonde\b|saber|duvida|informac|gostaria|\bposso\b|existe|previsao")


def classificar_urgencia(texto_norm: str) -> str:
    if _URG_ALTA.search(texto_norm):
        return "alta"
    if _URG_MEDIA.search(texto_norm):
        return "media"
    if _URG_BAIXA.search(texto_norm):
        return "baixa"
    return "media"


def pontuar_regras(texto_norm: str) -> dict:
    return {s: sum(w for rx, w in termos if rx.search(texto_norm)) for s, termos in _LEXICO_COMPILADO.items()}


def carregar_dataset(caminho=DATASET_PADRAO):
    textos, setores, urgencias = [], [], []
    with open(caminho, newline="", encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            textos.append(linha["texto"]); setores.append(linha["setor"]); urgencias.append(linha["urgencia"])
    return textos, setores, urgencias


def _montar_pipeline():
    uniao = FeatureUnion([
        ("palavras", TfidfVectorizer(preprocessor=normalizar, ngram_range=(1, 2), sublinear_tf=True)),
        ("caracteres", TfidfVectorizer(preprocessor=normalizar, analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True)),
    ])
    return Pipeline([("tfidf", uniao), ("clf", LogisticRegression(C=20, max_iter=1000))])


class Triagem:
    """Classificador híbrido. `margem`: vantagem mínima (em pontos) das regras;
    `limiar_ml`: probabilidade mínima para dispensar revisão manual."""

    def __init__(self, margem: float = 2.0, limiar_ml: float = 0.5):
        self.margem = margem
        self.limiar_ml = limiar_ml
        self.modelo = None

    def treinar(self, textos, rotulos):
        self.modelo = _montar_pipeline().fit(textos, rotulos)
        return self

    @classmethod
    def do_dataset(cls, caminho=DATASET_PADRAO, **kw):
        textos, setores, _ = carregar_dataset(caminho)
        return cls(**kw).treinar(textos, setores)

    # --- componentes
    def decisao_regras(self, texto_norm: str):
        """Retorna (setor|None, margem, pontuações). setor=None se sem decisão."""
        pont = pontuar_regras(texto_norm)
        ordenado = sorted(pont.items(), key=lambda kv: kv[1], reverse=True)
        (s1, p1), (_, p2) = ordenado[0], ordenado[1]
        return (s1 if p1 > 0 and p1 > p2 else None), p1 - p2, pont

    def decisao_ml(self, texto: str):
        proba = self.modelo.predict_proba([texto])[0]
        i = int(proba.argmax())
        return self.modelo.classes_[i], float(proba[i])

    # --- API principal
    def triar(self, texto: str, modo: str = "hibrido") -> dict:
        t0 = time.perf_counter()
        tn = normalizar(texto)
        setor_r, margem, pont = self.decisao_regras(tn)
        resultado = {"pontuacao_regras": pont}

        if modo == "regras":
            resultado.update(setor=setor_r or "indefinido", metodo="regras", confianca=None,
                             revisao_manual=setor_r is None)
        elif modo == "ml":
            s, p = self.decisao_ml(texto)
            resultado.update(setor=s, metodo="ml", confianca=round(p, 3), revisao_manual=p < self.limiar_ml)
        else:  # híbrido
            if setor_r is not None and margem >= self.margem and pont[setor_r] >= 2:
                resultado.update(setor=setor_r, metodo="regras", confianca=None, revisao_manual=False)
            else:
                s, p = self.decisao_ml(texto)
                resultado.update(setor=s, metodo="ml", confianca=round(p, 3), revisao_manual=p < self.limiar_ml)

        resultado["urgencia"] = classificar_urgencia(tn)
        resultado["tempo_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return resultado
