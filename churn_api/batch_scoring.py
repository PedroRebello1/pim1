"""
batch_scoring.py
================
Rotina mensal de pontuação (seção 5 do documento de arquitetura).

Fluxo: export -> limpeza -> anonimização (LGPD) -> atributos -> base por aluno
-> pontuação (modelo de produção) -> persistência em `predicoes` -> atualização
do cache (Redis) -> registro em `model_runs`. Idempotente por data de referência:
reprocessar um mês substitui apenas as predições daquele período.

Uso:
    python batch_scoring.py CAMINHO_DO_EXPORT.xls [--data-referencia 2026-06]
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import numpy as np

import churn_core as core
from config import config
from model_service import ModelService
from storage import MongoStore, RedisCache

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("batch")


def mes_corrente() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def executar(caminho_export: str, data_referencia: str | None = None) -> dict:
    data_referencia = data_referencia or mes_corrente()
    inicio = datetime.now(timezone.utc)
    run_id = f"run_{inicio.strftime('%Y%m%d%H%M%S')}"
    mongo, cache = MongoStore(), RedisCache()
    modelo = ModelService()
    status, erro = "sucesso", None

    try:
        log.info("[%s] Preparando base a partir de %s", run_id, caminho_export)
        df_parcelas = core.preparar_parcelas(caminho_export)          # limpa + anonimiza (LGPD)
        base = core.construir_base_aluno(df_parcelas, com_alvo=True)  # com_alvo só para registro
        n_alunos = len(base)
        taxa_churn = float(base[core.ALVO].mean()) if core.ALVO in base else None

        # Persiste a camada operacional (parcelas/alunos) — etapas 2 e 5 da seção 5.2 do doc.
        if mongo.disponivel:
            n_parc = mongo.substituir_parcelas(
                data_referencia, core.parcelas_para_docs(df_parcelas, data_referencia))
            mongo.substituir_alunos(
                data_referencia, core.alunos_para_docs(base, data_referencia))
            log.info("[%s] Coleções operacionais atualizadas: %d parcelas, %d alunos",
                     run_id, n_parc, n_alunos)
        else:
            log.warning("[%s] MongoDB indisponível — parcelas/alunos não persistidos.", run_id)

        log.info("[%s] Pontuando %d alunos com o modelo %s", run_id, n_alunos, modelo.versao)
        probs = modelo.prob_lote(base)

        agora = datetime.now(timezone.utc).isoformat()
        docs, cache_regs = [], {}
        for id_aluno, prob in zip(base["id_aluno"], probs):
            prob = float(prob)
            faixa = modelo.faixa(prob)
            doc = {
                "id_aluno": id_aluno,
                "prob_churn": round(prob, 4),
                "faixa_risco": faixa,
                "data_referencia": data_referencia,
                "modelo_versao": modelo.versao,
                "pontuado_em": agora,
            }
            docs.append(doc)
            cache_regs[id_aluno] = {
                "prob_churn": doc["prob_churn"], "faixa_risco": faixa,
                "data_referencia": data_referencia, "modelo_versao": modelo.versao,
            }

        n_alto = sum(1 for d in docs if d["faixa_risco"] == "Alto")

        if mongo.disponivel:
            log.info("[%s] Persistindo %d predições (substituindo %s)", run_id, len(docs), data_referencia)
            mongo.substituir_predicoes(data_referencia, docs)
        else:
            log.warning("[%s] MongoDB indisponível — predições não persistidas.", run_id)

        cache.set_many(cache_regs)

    except Exception as e:  # noqa: BLE001
        status, erro = "falha", str(e)
        n_alunos = n_alto = 0
        taxa_churn = None
        log.exception("[%s] Falha na execução", run_id)

    fim = datetime.now(timezone.utc)
    registro = {
        "run_id": run_id,
        "data_execucao": fim.isoformat(),
        "data_referencia": data_referencia,
        "n_alunos": int(n_alunos),
        "n_alto_risco": int(n_alto),
        "taxa_churn": taxa_churn,
        "modelo_versao": modelo.versao,
        "duracao_seg": round((fim - inicio).total_seconds(), 1),
        "status": status,
        "erro": erro,
    }
    if mongo.disponivel:
        mongo.registrar_execucao(registro)
    log.info("[%s] Concluído: %s", run_id, registro)
    return registro


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rotina mensal de pontuação de churn.")
    ap.add_argument("export", help="Caminho do export Excel da academia (.xls/.xlsx)")
    ap.add_argument("--data-referencia", default=None, help="Mês de competência (AAAA-MM)")
    args = ap.parse_args()
    executar(args.export, args.data_referencia)
