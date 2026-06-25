"""
verificar_atlas.py
==================
Confere o que foi gravado no MongoDB Atlas após a ingestão: conta os documentos
de cada coleção, lista as competências (data_referencia) e mostra um documento de
exemplo de `parcelas` — útil para confirmar, de olho, que NÃO há dados pessoais.

Uso (com MONGO_URI definido no .env ou no ambiente):
    python verificar_atlas.py
"""
from __future__ import annotations

from config import config
from storage import MongoStore

PII = {"nome", "telefone", "matricula", "empresa", "convenios"}


def main():
    mongo = MongoStore()
    if not mongo.disponivel:
        raise SystemExit("MongoDB indisponível — verifique MONGO_URI/rede (porta 27017).")

    cols = {
        "parcelas": config.COL_PARCELAS,
        "alunos": config.COL_ALUNOS,
        "predicoes": config.COL_PREDICOES,
        "model_runs": config.COL_MODEL_RUNS,
    }
    print(f"Banco: {config.MONGO_DB}\n")
    print(f"{'coleção':<12} {'documentos':>12}")
    print("-" * 26)
    for rotulo, nome in cols.items():
        print(f"{rotulo:<12} {mongo.db[nome].count_documents({}):>12,}")

    comps = mongo.db[config.COL_PARCELAS].distinct("data_referencia")
    print(f"\nCompetências em 'parcelas': {sorted(comps)}")

    doc = mongo.db[config.COL_PARCELAS].find_one(projection={"_id": 0})
    if doc:
        vazou = PII.intersection(doc.keys())
        print("\nExemplo de documento 'parcelas':")
        for k, v in doc.items():
            print(f"   {k:18s} = {v!r}")
        print("\nVerificação LGPD:",
              "OK — nenhum campo de PII presente." if not vazou else f"ALERTA: PII encontrada -> {vazou}")


if __name__ == "__main__":
    main()
