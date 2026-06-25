"""
config.py
=========
Configuração central via variáveis de ambiente (12-factor). Em desenvolvimento
pode-se usar um arquivo .env (ver .env.example); em produção as variáveis vêm do
orquestrador / cofre de segredos.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # python-dotenv é opcional; sem ele apenas o ambiente real é lido


def _f(nome: str, default: float) -> float:
    try:
        return float(os.getenv(nome, default))
    except (TypeError, ValueError):
        return default


class Config:
    # ----- Artefato do modelo -----------------------------------------------
    MODEL_PATH = os.getenv("MODEL_PATH", "modelos/churn_model.joblib")
    MODEL_META_PATH = os.getenv("MODEL_META_PATH", "modelos/churn_model.meta.json")

    # ----- Faixas de risco (privilegiam recall; ver doc, seção 7.3) ---------
    LIMIAR_ALTO = _f("LIMIAR_ALTO", 0.50)
    LIMIAR_MEDIO = _f("LIMIAR_MEDIO", 0.30)

    # ----- MongoDB (local em dev; MongoDB Atlas em produção) ----------------
    # Atlas usa um URI mongodb+srv://usuario:senha@cluster.xxxx.mongodb.net/...
    # (requer dnspython). TLS é ligado automaticamente para URIs srv/Atlas.
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB = os.getenv("MONGO_DB", "churn")
    MONGO_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "8000"))   # Atlas exige mais que o local
    MONGO_BATCH = int(os.getenv("MONGO_BATCH", "5000"))            # tamanho do lote no insert_many
    # "auto" (TLS para srv/atlas), "on" (força TLS) ou "off". Cobre Atlas atrás de proxy.
    MONGO_TLS = os.getenv("MONGO_TLS", "auto").strip().lower()
    COL_PARCELAS = os.getenv("COL_PARCELAS", "parcelas")
    COL_PREDICOES = os.getenv("COL_PREDICOES", "predicoes")
    COL_ALUNOS = os.getenv("COL_ALUNOS", "alunos")
    COL_MODEL_RUNS = os.getenv("COL_MODEL_RUNS", "model_runs")

    # ----- Redis (cache de baixa latência) ----------------------------------
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_TTL = int(os.getenv("REDIS_TTL", "2592000"))   # 30 dias (até o próximo ciclo)
    REDIS_PREFIX = os.getenv("REDIS_PREFIX", "risco:")

    # ----- API --------------------------------------------------------------
    API_TOKEN = os.getenv("API_TOKEN", "")               # vazio = sem autenticação (apenas dev)
    ALTO_RISCO_LIMIT = int(os.getenv("ALTO_RISCO_LIMIT", "100"))


config = Config()
