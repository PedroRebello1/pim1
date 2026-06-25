"""
train_model.py
==============
Treina e serializa o modelo de churn a partir de um export da academia.

Reproduz o notebook: compara cinco algoritmos por ROC-AUC em validação cruzada,
escolhe o melhor, **reajusta o vencedor sobre toda a base** (para produção) e
salva a Pipeline completa (pré-processamento + classificador) com `joblib`,
junto de um arquivo de metadados (versão, atributos, métricas, limiares).

Uso:
    python train_model.py CAMINHO_DO_EXPORT.xls [--out modelos/churn_model.joblib]
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import (accuracy_score, precision_score, recall_score,
                             f1_score, roc_auc_score, average_precision_score)

import churn_core as core
from config import config

try:
    from xgboost import XGBClassifier
    TEM_XGB = True
except Exception:
    TEM_XGB = False

RANDOM_STATE = 42
TEST_SIZE = 0.25


def construir_preprocessador() -> ColumnTransformer:
    try:
        ohe = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # sklearn < 1.2
        ohe = OneHotEncoder(handle_unknown="ignore", sparse=False)
    return ColumnTransformer([
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")),
                          ("sc", StandardScaler())]), core.NUM_FEATURES),
        ("cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent")),
                          ("oh", ohe)]), core.CAT_FEATURES),
    ])


def definir_modelos(y_tr):
    n_pos = int(np.sum(y_tr)); n_neg = int(len(y_tr) - n_pos)
    spw = n_neg / max(n_pos, 1)
    modelos = {
        "Regressão Logística": LogisticRegression(max_iter=1000, class_weight="balanced",
                                                  random_state=RANDOM_STATE),
        "Árvore de Decisão":   DecisionTreeClassifier(max_depth=5, class_weight="balanced",
                                                      random_state=RANDOM_STATE),
        "Random Forest":       RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                                      random_state=RANDOM_STATE),
        "Gradient Boosting":   GradientBoostingClassifier(random_state=RANDOM_STATE),
    }
    if TEM_XGB:
        modelos["XGBoost"] = XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.1,
                                           subsample=0.9, colsample_bytree=0.9,
                                           scale_pos_weight=spw, eval_metric="logloss",
                                           random_state=RANDOM_STATE)
    return modelos


def treinar(caminho_export: str, out_path: str, meta_path: str):
    print(f"[1/5] Carregando e preparando base a partir de: {caminho_export}")
    base = core.pipeline_completo(caminho_export, com_alvo=True)
    X, y = base[core.FEATURES], base[core.ALVO]
    print(f"      Alunos: {len(base)} | Taxa de churn: {y.mean()*100:.1f}%")

    pre = construir_preprocessador()
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=TEST_SIZE, stratify=y, random_state=RANDOM_STATE)
    modelos = definir_modelos(y_tr)

    print("[2/5] Comparando modelos (validação cruzada estratificada, 5 folds)...")
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    cv = {}
    for nome, est in modelos.items():
        pipe = Pipeline([("prep", pre), ("clf", est)])
        scores = cross_val_score(pipe, X, y, cv=skf, scoring="roc_auc")
        cv[nome] = (float(scores.mean()), float(scores.std()))
        print(f"      {nome:22s} ROC-AUC CV: {scores.mean():.3f} +/- {scores.std():.3f}")

    melhor = max(cv, key=lambda k: cv[k][0])
    print(f"[3/5] Melhor modelo por ROC-AUC CV: {melhor}")

    # métricas de teste do vencedor (relatório)
    pipe = Pipeline([("prep", pre), ("clf", modelos[melhor])])
    pipe.fit(X_tr, y_tr)
    p = pipe.predict_proba(X_te)[:, 1]
    yp = pipe.predict(X_te)
    metricas = {
        "acuracia": float(accuracy_score(y_te, yp)),
        "precisao": float(precision_score(y_te, yp, zero_division=0)),
        "recall":   float(recall_score(y_te, yp, zero_division=0)),
        "f1":       float(f1_score(y_te, yp, zero_division=0)),
        "roc_auc":  float(roc_auc_score(y_te, p)),
        "pr_auc":   float(average_precision_score(y_te, p)),
        "roc_auc_cv_media": cv[melhor][0],
        "roc_auc_cv_desvio": cv[melhor][1],
    }

    print("[4/5] Reajustando o vencedor sobre TODA a base (modelo de produção)...")
    modelo_prod = Pipeline([("prep", construir_preprocessador()), ("clf", modelos[melhor])])
    modelo_prod.fit(X, y)

    versao = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    meta = {
        "modelo_versao": versao,
        "algoritmo": melhor,
        "treinado_em": datetime.now(timezone.utc).isoformat(),
        "n_alunos_treino": int(len(base)),
        "taxa_churn_treino": float(y.mean()),
        "features": core.FEATURES,
        "num_features": core.NUM_FEATURES,
        "cat_features": core.CAT_FEATURES,
        "metricas_teste": metricas,
        "limiar_alto": config.LIMIAR_ALTO,
        "limiar_medio": config.LIMIAR_MEDIO,
    }

    print(f"[5/5] Salvando artefato em: {out_path}")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    joblib.dump({"pipeline": modelo_prod, "meta": meta}, out_path)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"OK. Versão do modelo: {versao} ({melhor}, ROC-AUC teste {metricas['roc_auc']:.3f})")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Treina e serializa o modelo de churn.")
    ap.add_argument("export", help="Caminho do export Excel da academia (.xls/.xlsx)")
    ap.add_argument("--out", default=config.MODEL_PATH)
    ap.add_argument("--meta", default=config.MODEL_META_PATH)
    args = ap.parse_args()
    treinar(args.export, args.out, args.meta)
