"""
model_service.py
================
Carrega o artefato serializado (Pipeline + metadados) e expõe a inferência.
Compartilhado pela API e pela rotina mensal de pontuação.
"""
from __future__ import annotations

import logging

import joblib
import pandas as pd

import churn_core as core
from config import config

log = logging.getLogger("model")


class ModelService:
    def __init__(self, path: str | None = None):
        self.path = path or config.MODEL_PATH
        self.pipeline = None
        self.meta: dict = {}
        self.carregar()

    def carregar(self):
        artefato = joblib.load(self.path)
        self.pipeline = artefato["pipeline"]
        self.meta = artefato.get("meta", {})
        log.info("Modelo carregado: versão %s (%s)",
                 self.meta.get("modelo_versao"), self.meta.get("algoritmo"))

    @property
    def versao(self) -> str:
        return self.meta.get("modelo_versao", "desconhecida")

    @property
    def limiar_alto(self) -> float:
        return float(self.meta.get("limiar_alto", config.LIMIAR_ALTO))

    @property
    def limiar_medio(self) -> float:
        return float(self.meta.get("limiar_medio", config.LIMIAR_MEDIO))

    # ----- inferência -------------------------------------------------------
    def prob_uma_linha(self, payload: dict) -> float:
        X = core.montar_dataframe_predicao(payload)
        return float(self.pipeline.predict_proba(X)[:, 1][0])

    def prob_lote(self, base: pd.DataFrame):
        """Recebe a base em nível de aluno (com a coluna id_aluno) e devolve as probabilidades."""
        X = base[core.FEATURES]
        return self.pipeline.predict_proba(X)[:, 1]

    def faixa(self, prob: float) -> str:
        return core.faixa_risco(prob, self.limiar_alto, self.limiar_medio)

    def avaliar(self, payload: dict) -> dict:
        prob = self.prob_uma_linha(payload)
        return {
            "prob_churn": round(prob, 4),
            "faixa_risco": self.faixa(prob),
            "modelo_versao": self.versao,
        }
