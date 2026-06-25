# API de Predição de Churn — CEUB

Implementa a **camada de serviço** e a **rotina mensal de pontuação** descritas no
documento de arquitetura, reaproveitando fielmente a lógica de dados do notebook
`Modelo_Churn_CEUB.ipynb` (carga, limpeza, anonimização LGPD e engenharia de
atributos). O modelo é tratado como um artefato servido, executado e versionado.

## Arquitetura em uma frase

`export mensal` → **ingestão** (limpeza → anonimização LGPD → atributos) → **MongoDB
Atlas** (`parcelas`, `alunos`) → **pontuação** do modelo → `predicoes` → atualiza
**Redis** (cache) → consumo pela **API Flask** e por dashboards de BI.

O banco operacional é o **MongoDB Atlas** (serviço gerenciado na nuvem); em
desenvolvimento aponta-se `MONGO_URI` para um MongoDB local. Todas as coleções são
**anonimizadas** — nenhum dado pessoal é persistido (ver *Privacidade*).

## Componentes

| Arquivo | Papel |
|---|---|
| `churn_core.py` | Lógica de dados do notebook (limpeza, anonimização LGPD, features anti-vazamento) + serialização das coleções `parcelas`/`alunos`. Fonte única para treino e inferência. |
| `ingest_parcelas.py` | **Ingestão** do export para o MongoDB Atlas: grava as coleções `parcelas` (transacional anonimizado) e `alunos` (base do modelo). É como o arquivo de ~71 mil parcelas entra no banco operacional. |
| `train_model.py` | Treina os 5 algoritmos, escolhe o melhor por ROC-AUC, reajusta na base toda e salva a Pipeline + metadados. |
| `model_service.py` | Carrega o artefato e expõe a inferência (probabilidade + faixa de risco). |
| `batch_scoring.py` | Rotina **mensal** (seção 5 do doc): pontua a base, grava `predicoes`, atualiza o cache e registra `model_runs`. |
| `app.py` | **API Flask** (seção 6 do doc). |
| `storage.py` | Acesso a MongoDB e Redis, com degradação graciosa quando indisponíveis. |
| `config.py` | Configuração via variáveis de ambiente. |
| `gerar_dados_exemplo.py` | Gera um export **sintético** para testar sem o arquivo real (que contém dados pessoais). |

## Início rápido (sem MongoDB/Redis)

```bash
pip install -r requirements.txt

# 1) gera dados sintéticos no formato do export real
python gerar_dados_exemplo.py --alunos 400 --saida exemplo_export.xlsx

# 2) treina e salva o modelo (modelos/churn_model.joblib)
python train_model.py exemplo_export.xlsx

# 3) sobe a API
python app.py            # dev
# gunicorn -w 4 -b 0.0.0.0:8000 app:app   # produção
```

Sem MongoDB/Redis a API funciona em modo degradado: `/predict` e `/health`
operam normalmente; os endpoints que dependem de predições persistidas
(`/aluno/<id>/risco`, `/alto-risco`) respondem `503`. Com os bancos disponíveis,
rode a rotina mensal para popular tudo:

```bash
python batch_scoring.py exemplo_export.xlsx --data-referencia 2026-06
```

## MongoDB Atlas — ingestão da base completa

O banco operacional em produção é o **MongoDB Atlas**. Para carregar o export real
(o arquivo com ~71 mil parcelas) no banco, anonimizado e pronto para consumo:

1. **Crie um cluster gratuito** no [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
   (tier M0 já basta para esta base), um usuário de banco e libere seu IP em
   *Network Access*.
2. **Copie o connection string** (botão *Connect → Drivers*), no formato
   `mongodb+srv://usuario:senha@cluster.xxxxx.mongodb.net/`.
3. **Configure** `MONGO_URI` no `.env` (ver `.env.example`). O TLS é ativado
   automaticamente para URIs do Atlas.
4. **Rode a ingestão:**

```bash
python ingest_parcelas.py "ParcelaEmAberto-...-1782162365081.xls" --data-referencia 2026-06
```

Isso grava duas coleções (idempotentes por `data_referencia`):

| Coleção | Conteúdo | Granularidade |
|---|---|---|
| `parcelas` | Histórico transacional anonimizado | 1 doc por parcela (~71 mil) |
| `alunos` | Base agregada em nível de aluno (entrada do modelo) | 1 doc por aluno (~4,4 mil) |

A rotina mensal (`batch_scoring.py`) também atualiza `parcelas`/`alunos` e grava as
`predicoes`. Treino e pontuação podem ler a base direto do Atlas
(`MongoStore.carregar_parcelas` + `churn_core.base_aluno_de_docs`), sem depender do arquivo.

Depois de carregar, confira o que foi gravado (contagens + checagem de ausência de PII):

```bash
python verificar_atlas.py
```

> **Conexão (porta 27017).** O Atlas aceita conexões apenas na porta **27017**. Redes
> corporativas costumam **bloquear a saída** nessa porta — o sintoma é o cliente
> resolver o DNS do cluster mas falhar com *"No replica set members found / ServerSelectionTimeoutError"*.
> Para diagnosticar: `Test-NetConnection portquiz.net -Port 27017` (Windows) — se der
> `False`, o firewall bloqueia a saída em 27017; rode a ingestão de uma rede que a permita.
> Lembre-se também de liberar seu IP em *Atlas → Network Access*.

### Runbook — carga ponta a ponta

Passo a passo para carregar o arquivo completo no Atlas a partir de uma máquina/rede
que libere a porta 27017 (ex.: rede doméstica ou roteamento por celular). Rode os
comandos **dentro da pasta `churn_api/`**.

**1. Dependências** (Python 3.10+):

```bash
python -m pip install pandas numpy openpyxl "pymongo[srv]" certifi python-dotenv
# alternativa completa: python -m pip install -r requirements.txt
```

**2. Connection string** — defina `MONGO_URI` no arquivo `churn_api/.env`
(veja `.env.example`):

```
MONGO_URI=mongodb+srv://usuario:senha@cluster.xxxxx.mongodb.net/?retryWrites=true&w=majority
```

**3. Ingestão** (o `.xls` fica um nível acima da pasta `churn_api/`):

```bash
python ingest_parcelas.py "..\ParcelaEmAberto-564fb28d84ebc24b0a5a3899b1b3355b-1782162365081.xls" --data-referencia 2026-06
```

Saída esperada (última linha do log):

```
Ingestão concluída: {'data_referencia': '2026-06', 'n_parcelas': 71321, 'n_alunos': 4364, 'colecoes': ['parcelas', 'alunos'], ...}
```

**4. Verificação** (contagens + amostra + checagem automática de PII):

```bash
python verificar_atlas.py
# esperado: parcelas 71.321 | alunos 4.364 | "Verificação LGPD: OK — nenhum campo de PII presente."
```

Também dá para conferir em *Atlas → Browse Collections → banco `churn` → coleções `parcelas`/`alunos`*.

**Notas**
- **Idempotência:** se a carga for interrompida, basta rodar de novo — ela substitui a
  competência (`data_referencia`), nunca duplica.
- **Mac/Linux:** use `python3` e o caminho `"../ParcelaEmAberto-...-1782162365081.xls"`.
- **Segurança:** após o uso, rotacione a senha do usuário do banco e troque o
  `0.0.0.0/0` por IPs específicos em *Network Access*.

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | Saúde do serviço e dependências |
| POST | `/predict` | Calcula o risco para atributos enviados (sem persistir) |
| GET | `/aluno/<id_aluno>/risco` | Risco mais recente (Redis → MongoDB) |
| GET | `/alto-risco?data_referencia=AAAA-MM&limite=N` | Lista priorizada de alto risco |
| POST | `/admin/reload-model` | Recarrega o artefato após retreino |

### Exemplo — `POST /predict`

```bash
curl -X POST http://localhost:8000/predict -H "Content-Type: application/json" -d '{
  "atraso_medio_dias": 12.5, "pct_em_dia": 0.2, "valor_medio": 150.0,
  "n_modalidades": 1, "n_telefones": 1, "modalidade_grupo": "Lutas",
  "plano": "Gold", "recorrencia": "Sim", "forma_pgto": "Cheque", "resp_proprio": 0
}'
# -> {"prob_churn": 0.47, "faixa_risco": "Médio", "modelo_versao": "..."}
```

Campos ausentes são tratados pelos imputers da Pipeline; categorias desconhecidas
são ignoradas pelo One-Hot Encoder.

## Configuração

Copie `.env.example` para `.env` e ajuste. Principais variáveis: `MODEL_PATH`,
`MONGO_URI`, `REDIS_URL`, `LIMIAR_ALTO`/`LIMIAR_MEDIO` (faixas de risco — definidas
para privilegiar o recall sobre quem tende a cancelar) e `API_TOKEN` (se definido,
exige `Authorization: Bearer <token>`).

## Agendamento da rotina mensal

`batch_scoring.py` é um job idempotente por `data_referencia`. Agende-o no início de
cada mês via cron / agendador de nuvem / orquestrador (Airflow, Prefect). Exemplo cron:

```
0 3 1 * *  cd /app && python batch_scoring.py /dados/export_mensal.xls
```

## Privacidade (LGPD)

A anonimização (`churn_core.anonimizar`) ocorre na ingestão, antes de qualquer
persistência: gera `id_aluno` artificial, guarda apenas a contagem de telefones e
descarta nome, telefone, matrícula original e identificação da academia. Nenhuma
coleção persiste dados pessoais; a reidentificação só é possível no sistema de origem.

## Observação

O artefato e o `exemplo_export.xlsx` gerados são **sintéticos**, só para demonstração.
Para produção, treine com o export real (`python train_model.py CAMINHO_REAL.xls`),
que reproduz o resultado do notebook (XGBoost, ROC-AUC ≈ 0,849).
