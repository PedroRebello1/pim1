"""
churn_core.py
=============
Lógica de dados compartilhada entre o treino, a rotina mensal de pontuação e a API.

Tudo aqui é uma extração fiel do notebook `Modelo_Churn_CEUB.ipynb`:
carga do export, limpeza/padronização, anonimização (LGPD) e engenharia de
atributos em nível de aluno (com as decisões anti-vazamento). Centralizar essa
lógica em um único módulo garante que a base usada no treino seja idêntica à
usada na inferência — requisito da arquitetura para evitar *training/serving skew*.
"""
from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 0. Esquema esperado e atributos do modelo
# ---------------------------------------------------------------------------
MAPA_COLUNAS = {
    "Matricula": "matricula", "Matrícula": "matricula", "Nome": "nome",
    "Telefone": "telefone", "Telefones": "telefone",
    "Contrato": "contrato", "Nr.Contrato": "contrato",
    "Parcela": "parcela", "Desc.Parcela": "parcela", "Cod.Parcela": "cod_parcela",
    "Situacao": "situacao", "Situação": "situacao",
    "Valor": "valor", "Dt.Cancelamento": "dt_cancelamento",
    "Recorrencia": "recorrencia", "Recorrência": "recorrencia",
    "Faturamento": "faturamento", "Dt.Faturamento": "faturamento",
    "Vencimento": "vencimento", "Dt.Vencimento": "vencimento",
    "Pagamento": "pagamento", "Dt.Pagamento": "pagamento",
    "Forma Pgto.": "forma_pgto", "Forma": "forma_pgto",
    "Modalidades": "modalidades", "Resp. Pgto.": "resp_pgto",
    "Turma": "turma", "Plano": "plano",
    "Empresa": "empresa", "Convênios": "convenios", "Convenios": "convenios",
}

COLUNAS_ESPERADAS = [
    "matricula", "nome", "telefone", "contrato", "parcela", "situacao",
    "valor", "recorrencia", "faturamento", "vencimento", "pagamento",
    "forma_pgto", "modalidades", "resp_pgto", "turma", "plano",
]

# Atributos que alimentam o modelo (a ordem é preservada na inferência).
NUM_FEATURES = [
    "atraso_medio_dias", "pct_em_dia", "valor_medio",
    "n_modalidades", "n_telefones", "resp_proprio",
]
CAT_FEATURES = ["modalidade_grupo", "plano", "recorrencia", "forma_pgto"]
FEATURES = NUM_FEATURES + CAT_FEATURES
ALVO = "churn"

# Parâmetros de redução de cardinalidade (iguais ao notebook).
TOP_PLANOS = 8
TOP_FORMAS = 6


# ---------------------------------------------------------------------------
# 1. Carga do export (Excel) — detecção de engine e de cabeçalho
# ---------------------------------------------------------------------------
def _engine_excel(caminho: str):
    """Escolhe o engine certo mesmo quando a extensão engana (ex.: .xlsx salvo como .xls)."""
    with open(caminho, "rb") as fh:
        assinatura = fh.read(8)
    if assinatura[:2] == b"PK":                       # ZIP  => Office Open XML (.xlsx)
        return "openpyxl"
    if assinatura[:4] == b"\xd0\xcf\x11\xe0":         # OLE2 => Excel 97-2003 (.xls real)
        return "xlrd"
    return None                                       # formato desconhecido: pandas infere


def carregar_dados(caminho: str) -> pd.DataFrame:
    """Lê o export detectando a linha de cabeçalho real (a que contém 'Matrícula')."""
    eng = _engine_excel(caminho)
    previa = pd.read_excel(caminho, header=None, nrows=5, engine=eng)
    linha_cab = 0
    for i in range(len(previa)):
        if previa.iloc[i].astype(str).str.contains("Matr", case=False, na=False).any():
            linha_cab = i
            break
    df = pd.read_excel(caminho, header=linha_cab, engine=eng)
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# 2. Limpeza e padronização
# ---------------------------------------------------------------------------
def _para_numero_brl(x):
    """Converte 'R$ 1.234,56' (e variações) em float; valores já numéricos passam direto."""
    if pd.isna(x):
        return np.nan
    if isinstance(x, (int, float)):
        return float(x)
    t = str(x).replace("R$", "").replace("\xa0", " ").strip()
    if "," in t:                       # formato BR: '.' separa milhar, ',' é decimal
        t = t.replace(".", "").replace(",", ".")
    return pd.to_numeric(t, errors="coerce")


def limpar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=MAPA_COLUNAS).copy()
    df = df.dropna(how="all")
    for col in COLUNAS_ESPERADAS:                      # garante o schema mesmo se a base não traz a coluna
        if col not in df.columns:
            df[col] = np.nan
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip()
        df[col] = df[col].replace({"-": np.nan, "nan": np.nan, "NaT": np.nan, "": np.nan})
    df["matricula"]   = df["matricula"].astype(str).str.strip()
    df["parcela_num"] = df["parcela"].astype(str).str.extract(r"(\d+)", expand=False).astype(float)
    df["valor"]       = df["valor"].map(_para_numero_brl).round(2)
    for c in ["faturamento", "vencimento", "pagamento"]:
        df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
    df["situacao"] = df["situacao"].str.title()
    df = df.drop_duplicates().reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 3. Anonimização (LGPD - Lei nº 13.709/2018) + atributos derivados de parcela
# ---------------------------------------------------------------------------
def _conta_itens(s) -> int:
    if pd.isna(s):
        return 0
    return len([x for x in str(s).split(",") if x.strip()])


def anonimizar(df: pd.DataFrame) -> pd.DataFrame:
    """Substitui PII por sinais não identificáveis. Nenhum dado pessoal segue adiante."""
    df = df.copy()
    df["n_telefones"] = df["telefone"].apply(_conta_itens)       # sinal não identificável
    codigos = pd.factorize(df["matricula"].astype(str))[0] + 1   # id artificial estável
    df["id_aluno"] = ["ALUNO_" + str(c).zfill(5) for c in codigos]
    sensiveis = ["matricula", "nome", "telefone", "empresa", "convenios"]
    df = df.drop(columns=[c for c in sensiveis if c in df.columns])
    return df[["id_aluno"] + [c for c in df.columns if c != "id_aluno"]]


def grupo_modalidade(m) -> str:
    if pd.isna(m):
        return "Desconhecida"
    t = str(m).lower()
    if "natac" in t or "nataç" in t:                    return "Natação"
    if "hidro" in t:                                    return "Hidroginástica"
    if "taekwondo" in t or "jiu" in t:                  return "Lutas"
    if "crosstraining" in t:                            return "Crosstraining"
    if ("," in t) or ("muscula" in t) or ("fitness" in t) or ("bike" in t):
        return "Academia/Fitness"
    return "Outros"


def derivar_atributos_parcela(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["modalidade_grupo"] = df["modalidades"].apply(grupo_modalidade)
    df["n_modalidades"]    = df["modalidades"].apply(_conta_itens)
    return df


# ---------------------------------------------------------------------------
# 4. Engenharia de atributos — base em nível de aluno (anti-vazamento)
# ---------------------------------------------------------------------------
def _reduzir_cardinalidade(serie: pd.Series, top: int, outro: str = "Outros") -> pd.Series:
    vals = serie.value_counts().nlargest(top).index
    return serie.where(serie.isin(vals), outro)


def _moda(serie: pd.Series, default=np.nan):
    s = serie.dropna()
    if len(s) == 0:
        return default
    m = s.mode()
    return m.iloc[0] if len(m) else default


def construir_base_aluno(df: pd.DataFrame, com_alvo: bool = True) -> pd.DataFrame:
    """Uma linha por aluno. Sinais calculados apenas sobre parcelas PAGAS (pré-cancelamento).

    Decisões anti-vazamento (fiéis ao notebook):
      * a situação 'Cancelado' é a origem do rótulo e nunca vira atributo;
      * não usamos contagens de parcelas (censuradas em pontos diferentes), só médias/taxas;
      * sem indicador de 'ausência de pagamento' (também seria proxy do rótulo).
    """
    df = df.sort_values(["id_aluno", "parcela_num"]).copy()
    linhas = []
    for id_aluno, g in df.groupby("id_aluno"):
        churn  = int((g["situacao"] == "Cancelado").any())
        pagas  = g[g["situacao"] == "Pago"]
        atraso = (pagas["pagamento"] - pagas["vencimento"]).dt.days
        ref    = pagas if len(pagas) else g
        linha = {
            "id_aluno":          id_aluno,
            "atraso_medio_dias": atraso.mean() if len(atraso) else np.nan,
            "pct_em_dia":        (atraso <= 0).mean() if len(atraso) else np.nan,
            "valor_medio":       g["valor"].mean(),
            "n_modalidades":     _moda(ref.get("n_modalidades", pd.Series(dtype=float)), 0),
            "n_telefones":       _moda(ref.get("n_telefones", pd.Series(dtype=float)), 0),
            "modalidade_grupo":  _moda(ref.get("modalidade_grupo", pd.Series(dtype=object)), "Desconhecida"),
            "plano":             _moda(ref.get("plano", pd.Series(dtype=object)), "Desconhecido"),
            "recorrencia":       _moda(ref.get("recorrencia", pd.Series(dtype=object)), "Não"),
            "forma_pgto":        _moda(pagas["forma_pgto"], "Desconhecida") if len(pagas) else "Desconhecida",
            "resp_proprio":      0,   # constante nesta base (resp_pgto ausente + nome anonimizado)
        }
        if com_alvo:
            linha["churn"] = churn
        linhas.append(linha)

    base = pd.DataFrame(linhas)
    base["plano"]      = _reduzir_cardinalidade(base["plano"], TOP_PLANOS)
    base["forma_pgto"] = _reduzir_cardinalidade(base["forma_pgto"], TOP_FORMAS)
    return base


def preparar_parcelas(caminho_export: str) -> pd.DataFrame:
    """Carga → limpeza → anonimização (LGPD) → atributos de parcela.

    Devolve a base TRANSACIONAL em nível de parcela, já anonimizada (sem PII):
    é a fonte da coleção `parcelas` (MongoDB) e a entrada para a base por aluno.
    Concentrar a anonimização aqui garante que nada de pessoal seja persistido.
    """
    df = carregar_dados(caminho_export)
    df = limpar(df)
    df = anonimizar(df)
    df = derivar_atributos_parcela(df)
    return df


def pipeline_completo(caminho_export: str, com_alvo: bool = True) -> pd.DataFrame:
    """Atalho: do arquivo bruto à base em nível de aluno (carga → limpeza → anonimização → features)."""
    return construir_base_aluno(preparar_parcelas(caminho_export), com_alvo=com_alvo)


# ---------------------------------------------------------------------------
# 5. Inferência: montar a linha de atributos para um aluno (endpoint /predict)
# ---------------------------------------------------------------------------
def montar_dataframe_predicao(payload: dict) -> pd.DataFrame:
    """Constrói um DataFrame de 1 linha com exatamente as colunas que o modelo espera.

    Campos ausentes viram NaN (numéricos) ou None (categóricos): os imputers da
    Pipeline preenchem, e o OneHotEncoder ignora categorias desconhecidas.
    """
    linha = {}
    for f in NUM_FEATURES:
        v = payload.get(f, np.nan)
        try:
            linha[f] = float(v) if v is not None and v != "" else np.nan
        except (TypeError, ValueError):
            linha[f] = np.nan
    for f in CAT_FEATURES:
        v = payload.get(f, None)
        linha[f] = v if v not in ("", None) else None
    return pd.DataFrame([linha], columns=FEATURES)


def faixa_risco(prob: float, limiar_alto: float, limiar_medio: float) -> str:
    """Traduz a probabilidade em faixa acionável. Limiares configuráveis (ver config.py)."""
    if prob >= limiar_alto:
        return "Alto"
    if prob >= limiar_medio:
        return "Médio"
    return "Baixo"


# ---------------------------------------------------------------------------
# 6. Serialização para o MongoDB (camada operacional — doc de arquitetura, 4.2)
# ---------------------------------------------------------------------------
# Colunas da base de parcela que viram documentos da coleção `parcelas`. Todas
# já são anonimizadas (sem PII): a reidentificação só existe no sistema de origem.
PARCELA_DOC_COLS = [
    "id_aluno", "contrato", "parcela", "parcela_num", "cod_parcela",
    "situacao", "valor", "dt_cancelamento", "recorrencia",
    "faturamento", "vencimento", "pagamento", "forma_pgto",
    "modalidades", "modalidade_grupo", "n_modalidades",
    "plano", "turma", "n_telefones",
]


def _valor_mongo(v):
    """Converte um escalar do pandas/numpy para um tipo nativo aceito pelo BSON.

    Timestamps viram datetime; NaN/NaT/None viram None; escalares numpy viram
    int/float do Python. Strings e demais tipos passam direto.

    Obs.: `pd.NaT` é subclasse de datetime, por isso o tratamento de nulos vem
    antes de qualquer passthrough de data — senão um NaT chegaria ao BSON e falharia.
    """
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass                                    # valor não-escalar: segue adiante
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.generic):
        return v.item()
    return v


def parcelas_para_docs(df_parcelas: pd.DataFrame, data_referencia: str) -> list[dict]:
    """Converte a base de parcelas (anonimizada) em documentos prontos para o MongoDB.

    Cada documento recebe a `data_referencia` (mês de competência), o que torna a
    carga idempotente: reprocessar um mês substitui apenas as parcelas daquele período.
    """
    df = df_parcelas.copy()
    if "dt_cancelamento" in df.columns:                 # vinha como texto da limpeza
        df["dt_cancelamento"] = pd.to_datetime(df["dt_cancelamento"], errors="coerce", dayfirst=True)
    cols = [c for c in PARCELA_DOC_COLS if c in df.columns]
    docs = []
    for reg in df[cols].to_dict(orient="records"):
        doc = {k: _valor_mongo(v) for k, v in reg.items()}
        doc["data_referencia"] = data_referencia
        docs.append(doc)
    return docs


def alunos_para_docs(base: pd.DataFrame, data_referencia: str) -> list[dict]:
    """Converte a base em nível de aluno (entrada do modelo) em documentos `alunos`."""
    docs = []
    for reg in base.to_dict(orient="records"):
        doc = {k: _valor_mongo(v) for k, v in reg.items()}
        doc["data_referencia"] = data_referencia
        docs.append(doc)
    return docs


def base_aluno_de_docs(docs, com_alvo: bool = True) -> pd.DataFrame:
    """Reconstrói a base em nível de aluno a partir de documentos `parcelas` do MongoDB.

    Permite treinar/pontuar lendo a camada operacional (Atlas) em vez do arquivo,
    preservando a mesma lógica anti-vazamento do notebook (`construir_base_aluno`).
    """
    df = pd.DataFrame(list(docs))
    if df.empty:
        return pd.DataFrame()
    for c in ["faturamento", "vencimento", "pagamento"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return construir_base_aluno(df, com_alvo=com_alvo)
