"""
ingest_parcelas.py
==================
Ingestão do export de parcelas para o MongoDB (camada operacional — seção 3 e
etapas 1-5 da seção 5.2 do documento de arquitetura).

Lê o arquivo Excel da academia (ex.: o export completo com ~71 mil parcelas),
aplica limpeza, padronização e **anonimização (LGPD)** e grava:
  * a coleção `parcelas`  — histórico transacional anonimizado (1 doc por parcela);
  * a coleção `alunos`    — base agregada em nível de aluno (entrada do modelo).

Nenhum dado pessoal é gravado: a anonimização ocorre na ingestão, antes de
qualquer persistência (id_aluno artificial; do telefone guarda-se só a contagem;
nome, telefone, matrícula original e identificação da academia são descartados).

A carga é idempotente por `data_referencia`: reprocessar um mês substitui apenas
as parcelas/alunos daquele período.

Pré-requisitos:
    * Definir MONGO_URI (em produção, o cluster do MongoDB Atlas) no ambiente
      ou no arquivo .env — ex.: mongodb+srv://usuario:senha@cluster.xxxx.mongodb.net/

Uso:
    python ingest_parcelas.py CAMINHO_DO_EXPORT.xls [--data-referencia 2026-06]
    python ingest_parcelas.py CAMINHO_DO_EXPORT.xls --sem-alunos   # só a coleção parcelas
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import churn_core as core
from config import config
from storage import MongoStore

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("ingest")


def mes_corrente() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def executar(caminho_export: str, data_referencia: str | None = None,
             com_alunos: bool = True) -> dict:
    data_referencia = data_referencia or mes_corrente()
    inicio = datetime.now(timezone.utc)

    log.info("Preparando parcelas (limpeza + anonimização LGPD) a partir de %s", caminho_export)
    df_parcelas = core.preparar_parcelas(caminho_export)
    docs_parc = core.parcelas_para_docs(df_parcelas, data_referencia)
    n_parcelas = len(docs_parc)
    n_alunos = int(df_parcelas["id_aluno"].nunique())
    log.info("Parcelas: %d | Alunos únicos: %d | Competência: %s",
             n_parcelas, n_alunos, data_referencia)

    mongo = MongoStore()
    if not mongo.disponivel:
        raise SystemExit(
            "MongoDB indisponível. Defina MONGO_URI (ex.: o cluster do MongoDB Atlas) "
            "no ambiente ou no .env e tente novamente.")

    log.info("Gravando coleção '%s' (substituindo a competência %s)...",
             config.COL_PARCELAS, data_referencia)
    mongo.substituir_parcelas(data_referencia, docs_parc)

    if com_alunos:
        base = core.construir_base_aluno(df_parcelas, com_alvo=True)
        docs_al = core.alunos_para_docs(base, data_referencia)
        log.info("Gravando coleção '%s' (%d alunos)...", config.COL_ALUNOS, len(docs_al))
        mongo.substituir_alunos(data_referencia, docs_al)

    dur = round((datetime.now(timezone.utc) - inicio).total_seconds(), 1)
    resumo = {
        "data_referencia": data_referencia,
        "n_parcelas": n_parcelas,
        "n_alunos": n_alunos,
        "colecoes": [config.COL_PARCELAS] + ([config.COL_ALUNOS] if com_alunos else []),
        "duracao_seg": dur,
    }
    log.info("Ingestão concluída: %s", resumo)
    return resumo


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ingestão do export de parcelas para o MongoDB (Atlas).")
    ap.add_argument("export", help="Caminho do export Excel da academia (.xls/.xlsx)")
    ap.add_argument("--data-referencia", default=None, help="Mês de competência (AAAA-MM)")
    ap.add_argument("--sem-alunos", action="store_true",
                    help="Grava apenas a coleção 'parcelas' (não recalcula 'alunos')")
    args = ap.parse_args()
    executar(args.export, args.data_referencia, com_alunos=not args.sem_alunos)
