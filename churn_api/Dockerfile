FROM python:3.11-slim

WORKDIR /app

# dependências do sistema mínimas para xgboost/scikit-learn
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Servidor de produção (a rotina mensal roda fora, via orquestrador).
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "--timeout", "60", "app:app"]
