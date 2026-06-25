# Instruções — MongoDB Atlas

Passo a passo para criar o cluster e obter a connection string usada pela ingestão
(`churn_api/ingest_parcelas.py`).

## 1. Create the account / cluster
- Go to https://www.mongodb.com/cloud/atlas/register and sign up (Google login is fine).
- When prompted to deploy, choose **M0 — Free**.
- Provider/region: **AWS**, region **São Paulo (sa-east-1)** (closest to Brazil → lowest latency).
- Name the cluster (e.g. `churn-ceub`) and click **Create Deployment**. It takes ~1–3 min to provision.

## 2. Database user (login for the app)
Atlas usually pops a "Connect to … / Create a database user" box right after. Otherwise: left menu **Security → Database Access → + Add New Database User**.

- Auth method: **Password**.
- Username: e.g. `churn_app`
- Password: click **Autogenerate Secure Password** and copy it somewhere (or set your own — if you use symbols like `@ : / #`, they must be percent-encoded in the URI, so a plain alphanumeric password is easier).
- Built-in role: **Read and write to any database**.
- **Add User**.

## 3. Network access (allow your machine to connect)
Left menu **Security → Network Access → + Add IP Address**:

- For a quick demo: **Allow Access from Anywhere** (`0.0.0.0/0`), or
- More secure: **Add Current IP Address**.
- **Confirm**.

## 4. Get the connection string
Left menu **Database → Connect** (on your cluster) → **Drivers** → Driver **Python**. Copy the string — it looks like:

```
mongodb+srv://churn_app:<db_password>@churn-ceub.xxxxx.mongodb.net/?retryWrites=true&w=majority&appName=churn-ceub
```

Replace `<db_password>` with the password from step 2. (You don't need to add a database name — the code uses `MONGO_DB=churn` by default.)

## 5. Próximo passo — carregar a base
Coloque a string em `churn_api/.env` (`MONGO_URI=...`) e rode a carga a partir de uma
rede que libere a porta **27017** (ver o *Runbook* em `README.md`):

```bash
python ingest_parcelas.py "..\ParcelaEmAberto-564fb28d84ebc24b0a5a3899b1b3355b-1782162365081.xls" --data-referencia 2026-06
python verificar_atlas.py
```
