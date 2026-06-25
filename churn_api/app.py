"""
app.py
======
API de predição de churn (Flask), conforme a seção 6 do documento de arquitetura.

Endpoints:
    GET  /health                 -> verificação de saúde e dependências
    GET  /aluno/<id_aluno>/risco -> risco mais recente (Redis -> MongoDB)
    POST /predict                -> calcula o risco para atributos enviados (sem persistir)
    GET  /alto-risco             -> lista priorizada de alunos em alto risco do mês corrente
    POST /admin/reload-model     -> recarrega o artefato do modelo (operação administrativa)

Padrão de uso (doc, seção 6):
    * leitura de risco já calculado -> consulta rápida servida pelo Redis com fallback no MongoDB;
    * predição sob demanda          -> aplica a Pipeline a atributos informados, útil para simulações.

Execução (desenvolvimento):
    python app.py
Produção (recomendado):
    gunicorn -w 4 -b 0.0.0.0:8000 app:app
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import Flask, jsonify, request

import churn_core as core
from config import config
from model_service import ModelService
from storage import MongoStore, RedisCache

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("api")

app = Flask(__name__)

# ---- dependências carregadas uma vez no start-up --------------------------
try:
    modelo = ModelService()
    MODELO_OK = True
except Exception as e:  # noqa: BLE001
    modelo = None
    MODELO_OK = False
    log.error("Falha ao carregar o modelo: %s. Treine com train_model.py.", e)

mongo = MongoStore()
cache = RedisCache()


# ---- autenticação opcional por token --------------------------------------
def requer_token(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if config.API_TOKEN:
            enviado = request.headers.get("Authorization", "").replace("Bearer ", "")
            if enviado != config.API_TOKEN:
                return jsonify({"erro": "não autorizado"}), 401
        return f(*args, **kwargs)
    return wrapper


def _erro(msg, code):
    return jsonify({"erro": msg}), code


# ===========================================================================
# Endpoints
# ===========================================================================
@app.get("/health")
def health():
    return jsonify({
        "status": "ok" if MODELO_OK else "degradado",
        "modelo_carregado": MODELO_OK,
        "modelo_versao": modelo.versao if MODELO_OK else None,
        "algoritmo": modelo.meta.get("algoritmo") if MODELO_OK else None,
        "mongodb": mongo.disponivel,
        "redis": cache.disponivel,
    })


@app.get("/aluno/<id_aluno>/risco")
@requer_token
def risco_aluno(id_aluno: str):
    """Risco mais recente do aluno: tenta o cache (Redis); se falhar, o MongoDB."""
    em_cache = cache.get(id_aluno)
    if em_cache:
        return jsonify({**em_cache, "id_aluno": id_aluno, "origem": "cache"})

    doc = mongo.ultima_predicao(id_aluno)
    if doc:
        cache.set(id_aluno, {
            "prob_churn": doc.get("prob_churn"),
            "faixa_risco": doc.get("faixa_risco"),
            "data_referencia": doc.get("data_referencia"),
            "modelo_versao": doc.get("modelo_versao"),
        })
        return jsonify({**doc, "origem": "mongodb"})

    if not mongo.disponivel:
        return _erro("MongoDB indisponível; não há predições persistidas para consultar.", 503)
    return _erro(f"Nenhuma predição encontrada para o aluno {id_aluno}.", 404)


@app.post("/predict")
@requer_token
def predict():
    """Calcula o risco para os atributos enviados, sem persistir (simulação/on-demand)."""
    if not MODELO_OK:
        return _erro("Modelo não carregado. Execute train_model.py.", 503)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _erro("Envie um corpo JSON com os atributos do aluno.", 400)

    resultado = modelo.avaliar(payload)
    resultado["atributos_esperados"] = core.FEATURES
    return jsonify(resultado)


@app.get("/alto-risco")
@requer_token
def alto_risco():
    """Lista priorizada de alunos em alto risco do mês corrente (ou de uma data_referencia)."""
    if not mongo.disponivel:
        return _erro("MongoDB indisponível; a lista depende de predições persistidas.", 503)
    data_ref = request.args.get("data_referencia")
    try:
        limite = min(int(request.args.get("limite", config.ALTO_RISCO_LIMIT)), 1000)
    except (TypeError, ValueError):
        limite = config.ALTO_RISCO_LIMIT

    itens = mongo.alto_risco(data_referencia=data_ref, limite=limite)
    return jsonify({
        "data_referencia": data_ref or mongo.ultima_data_referencia(),
        "total": len(itens),
        "alunos": itens,
    })


@app.post("/admin/reload-model")
@requer_token
def reload_model():
    """Recarrega o artefato do modelo sem reiniciar o processo (ex.: após retreino)."""
    global MODELO_OK
    try:
        modelo.carregar()
        MODELO_OK = True
        return jsonify({"status": "recarregado", "modelo_versao": modelo.versao})
    except Exception as e:  # noqa: BLE001
        return _erro(f"Falha ao recarregar: {e}", 500)


@app.get("/")
def index():
    return jsonify({
        "servico": "API de Predição de Churn — CEUB",
        "endpoints": [
            "GET  /health",
            "GET  /aluno/<id_aluno>/risco",
            "POST /predict",
            "GET  /alto-risco?data_referencia=AAAA-MM&limite=N",
            "POST /admin/reload-model",
        ],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
