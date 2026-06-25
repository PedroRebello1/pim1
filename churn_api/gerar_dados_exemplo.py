"""
gerar_dados_exemplo.py
======================
Gera um export Excel SINTÉTICO no mesmo formato do sistema da academia
(linha de título acima do cabeçalho, valores em R$, datas BR), apenas para
testar a API e a rotina sem precisar do arquivo real (que contém dados pessoais).

NÃO são dados reais — servem só para desenvolvimento/demonstração.

Uso:
    python gerar_dados_exemplo.py --alunos 400 --saida exemplo_export.xlsx
"""
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

import numpy as np
import pandas as pd

PLANOS = ["Gold", "Black", "Fit", "Smart", "Premium", "Light", "Plus", "Trimestral", "Anual"]
FORMAS = ["Cartao", "Pix", "Boleto", "Debito em Conta", "Cheque", "Dinheiro", "Cartao Recorrente"]
MODALIDADES = ["Musculacao", "Musculacao, Fitness", "Natacao", "Hidroginastica",
               "Crosstraining", "Taekwondo", "Jiu-jitsu", "Bike, Musculacao"]


def gerar(n_alunos: int, seed: int = 7) -> pd.DataFrame:
    random.seed(seed); np.random.seed(seed)
    linhas = []
    base = date(2024, 1, 5)
    for i in range(1, n_alunos + 1):
        matricula = 100000 + i
        plano = random.choice(PLANOS)
        forma = random.choice(FORMAS)
        modal = random.choice(MODALIDADES)
        recorrencia = random.choice(["Sim", "Não"])
        n_tel = random.choice([1, 1, 1, 2])
        telefones = ", ".join(["619" + str(random.randint(10000000, 99999999)) for _ in range(n_tel)])
        valor = round(random.uniform(80, 320), 2)

        # sinal sintético: cheque/recorrente + atraso alto -> maior risco de churn
        risco = 0.15
        if forma in ("Cheque", "Cartao Recorrente"):
            risco += 0.35
        if recorrencia == "Sim":
            risco += 0.15
        atraso_base = np.random.poisson(2) + (8 if risco > 0.4 else 0)
        vai_cancelar = random.random() < min(risco, 0.85)

        n_parc = random.randint(6, 14)
        cancel_em = random.randint(3, n_parc) if vai_cancelar else None
        for p in range(1, n_parc + 1):
            venc = base + timedelta(days=30 * (p - 1) + i % 20)
            if cancel_em and p >= cancel_em:
                situacao, pgto, vlr = "Cancelado", None, valor
            else:
                situacao = "Pago"
                atraso = max(0, int(np.random.normal(atraso_base, 3)))
                pgto = venc + timedelta(days=atraso)
                vlr = valor
            linhas.append({
                "Matrícula": matricula,
                "Nome": f"Aluno Teste {i}",
                "Telefone": telefones,
                "Nr.Contrato": 900000 + i,
                "Desc.Parcela": f"Parcela {p}/{n_parc}",
                "Situação": situacao,
                "Valor": f"R$ {vlr:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "Recorrência": recorrencia,
                "Dt.Faturamento": venc.strftime("%d/%m/%Y"),
                "Dt.Vencimento": venc.strftime("%d/%m/%Y"),
                "Dt.Pagamento": pgto.strftime("%d/%m/%Y") if pgto else "-",
                "Forma Pgto.": forma,
                "Modalidades": modal,
                "Plano": plano,
            })
    return pd.DataFrame(linhas)


def salvar(df: pd.DataFrame, caminho: str):
    # escreve com uma linha de TÍTULO acima do cabeçalho real, como o export verdadeiro
    with pd.ExcelWriter(caminho, engine="openpyxl") as xw:
        pd.DataFrame([["Relatório de Parcelas — Academia (EXEMPLO SINTÉTICO)"]]).to_excel(
            xw, index=False, header=False, startrow=0)
        df.to_excel(xw, index=False, startrow=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--alunos", type=int, default=400)
    ap.add_argument("--saida", default="exemplo_export.xlsx")
    args = ap.parse_args()
    df = gerar(args.alunos)
    salvar(df, args.saida)
    print(f"Gerado {args.saida}: {len(df)} parcelas, {args.alunos} alunos (dados sintéticos).")
