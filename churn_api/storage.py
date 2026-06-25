"""
storage.py
==========
Camada de acesso a dados da API: MongoDB (banco operacional) e Redis (cache).

Ambos são opcionais em tempo de execução. Se um deles não estiver disponível, a
API continua funcionando em modo degradado — útil para demonstração local:
  * sem Redis  -> as leituras vão direto ao MongoDB;
  * sem Mongo  -> os endpoints que dependem de predições persistidas respondem 503,
                  mas /predict (somente modelo) segue operando.
"""
from __future__ import annotations

import json
import logging

from config import config

log = logging.getLogger("storage")


# ===========================================================================
# MongoDB
# ===========================================================================
def _uri_segura(uri: str) -> str:
    """Oculta a senha do URI para registro em log (usuario:senha@ -> usuario:***@)."""
    if "@" not in uri or "://" not in uri:
        return uri
    esquema, resto = uri.split("://", 1)
    cred, _, host = resto.partition("@")
    if ":" in cred:
        usuario = cred.split(":", 1)[0]
        cred = f"{usuario}:***"
    return f"{esquema}://{cred}@{host}"


def _usar_tls() -> bool:
    """Decide se a conexão deve usar TLS. Atlas (mongodb+srv) exige TLS sempre."""
    modo = config.MONGO_TLS
    if modo == "on":
        return True
    if modo == "off":
        return False
    uri = config.MONGO_URI.lower()                       # modo "auto"
    return uri.startswith("mongodb+srv://") or "mongodb.net" in uri or "tls=true" in uri


class MongoStore:
    def __init__(self):
        self.disponivel = False
        self.db = None
        try:
            from pymongo import MongoClient, ASCENDING, DESCENDING
            self._ASC, self._DESC = ASCENDING, DESCENDING
            kwargs = {"serverSelectionTimeoutMS": config.MONGO_TIMEOUT_MS}
            if _usar_tls():
                import certifi
                kwargs["tls"] = True
                kwargs["tlsCAFile"] = certifi.where()    # CA confiável (evita falha de TLS no Windows)
            client = MongoClient(config.MONGO_URI, **kwargs)
            client.admin.command("ping")
            self.db = client[config.MONGO_DB]
            self._garantir_indices()
            self.disponivel = True
            log.info("MongoDB conectado: %s/%s", _uri_segura(config.MONGO_URI), config.MONGO_DB)
        except Exception as e:  # noqa: BLE001
            log.warning("MongoDB indisponível (%s). Operando em modo degradado.", e)

    def _garantir_indices(self):
        pred = self.db[config.COL_PREDICOES]
        pred.create_index([("id_aluno", self._ASC), ("data_referencia", self._DESC)])
        pred.create_index([("data_referencia", self._DESC), ("prob_churn", self._DESC)])
        parc = self.db[config.COL_PARCELAS]
        parc.create_index([("id_aluno", self._ASC), ("data_referencia", self._DESC)])
        parc.create_index([("data_referencia", self._DESC)])
        self.db[config.COL_ALUNOS].create_index(
            [("id_aluno", self._ASC), ("data_referencia", self._DESC)])

    # ----- leitura ----------------------------------------------------------
    def ultima_predicao(self, id_aluno: str):
        if not self.disponivel:
            return None
        return self.db[config.COL_PREDICOES].find_one(
            {"id_aluno": id_aluno},
            sort=[("data_referencia", self._DESC)],
            projection={"_id": 0},
        )

    def ultima_data_referencia(self):
        if not self.disponivel:
            return None
        doc = self.db[config.COL_PREDICOES].find_one(
            sort=[("data_referencia", self._DESC)], projection={"data_referencia": 1, "_id": 0}
        )
        return doc["data_referencia"] if doc else None

    def alto_risco(self, data_referencia: str | None = None, limite: int = 100):
        if not self.disponivel:
            return None
        data_referencia = data_referencia or self.ultima_data_referencia()
        if data_referencia is None:
            return []
        cur = (
            self.db[config.COL_PREDICOES]
            .find({"data_referencia": data_referencia, "faixa_risco": "Alto"},
                  projection={"_id": 0})
            .sort("prob_churn", self._DESC)
            .limit(limite)
        )
        return list(cur)

    def carregar_parcelas(self, data_referencia: str | None = None) -> list[dict]:
        """Lê as parcelas anonimizadas da camada operacional (para treino/pontuação a partir do Atlas)."""
        if not self.disponivel:
            return []
        data_referencia = data_referencia or self.ultima_data_parcelas()
        filtro = {"data_referencia": data_referencia} if data_referencia else {}
        return list(self.db[config.COL_PARCELAS].find(filtro, projection={"_id": 0}))

    def ultima_data_parcelas(self):
        if not self.disponivel:
            return None
        doc = self.db[config.COL_PARCELAS].find_one(
            sort=[("data_referencia", self._DESC)], projection={"data_referencia": 1, "_id": 0})
        return doc["data_referencia"] if doc else None

    # ----- escrita (usada pela ingestão e pela rotina mensal) ---------------
    def _substituir_em_lote(self, colecao: str, data_referencia: str, docs: list[dict]):
        """Idempotente por mês: apaga a competência e reinsere em lotes (grandes volumes)."""
        col = self.db[colecao]
        col.delete_many({"data_referencia": data_referencia})
        lote = max(int(config.MONGO_BATCH), 1)
        for ini in range(0, len(docs), lote):
            col.insert_many(docs[ini:ini + lote], ordered=False)
        return len(docs)

    def substituir_parcelas(self, data_referencia: str, docs: list[dict]) -> int:
        return self._substituir_em_lote(config.COL_PARCELAS, data_referencia, docs)

    def substituir_alunos(self, data_referencia: str, docs: list[dict]) -> int:
        return self._substituir_em_lote(config.COL_ALUNOS, data_referencia, docs)

    def substituir_predicoes(self, data_referencia: str, docs: list[dict]) -> int:
        return self._substituir_em_lote(config.COL_PREDICOES, data_referencia, docs)

    def registrar_execucao(self, doc: dict):
        self.db[config.COL_MODEL_RUNS].insert_one(doc)


# ===========================================================================
# Redis
# ===========================================================================
class RedisCache:
    def __init__(self):
        self.disponivel = False
        self.r = None
        try:
            import redis
            self.r = redis.from_url(config.REDIS_URL, socket_connect_timeout=1.5,
                                    decode_responses=True)
            self.r.ping()
            self.disponivel = True
            log.info("Redis conectado: %s", config.REDIS_URL)
        except Exception as e:  # noqa: BLE001
            log.warning("Redis indisponível (%s). Cache desativado.", e)

    def _chave(self, id_aluno: str) -> str:
        return f"{config.REDIS_PREFIX}{id_aluno}"

    def get(self, id_aluno: str):
        if not self.disponivel:
            return None
        try:
            val = self.r.get(self._chave(id_aluno))
            return json.loads(val) if val else None
        except Exception:  # noqa: BLE001
            return None

    def set(self, id_aluno: str, dados: dict):
        if not self.disponivel:
            return
        try:
            self.r.set(self._chave(id_aluno), json.dumps(dados), ex=config.REDIS_TTL)
        except Exception:  # noqa: BLE001
            pass

    def set_many(self, registros: dict[str, dict]):
        if not self.disponivel or not registros:
            return
        try:
            pipe = self.r.pipeline()
            for id_aluno, dados in registros.items():
                pipe.set(self._chave(id_aluno), json.dumps(dados), ex=config.REDIS_TTL)
            pipe.execute()
        except Exception:  # noqa: BLE001
            pass
