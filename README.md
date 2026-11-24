# CRM (Python + Flask)

## Rodar localmente
    pip install -r requirements.txt
    python app.py        # http://localhost:8080
Sem credenciais do WhatsApp o sistema funciona em modo teste (não envia de verdade).

## Variáveis de ambiente
SECRET_KEY (obrigatória em produção) · DATABASE_URL (Postgres) · REGISTER_CODE (código de convite p/ cadastro)
WA_TOKEN · WA_PHONE_ID · WA_VERIFY_TOKEN (WhatsApp Cloud API da Meta)

## Publicar no Google Cloud Run
    gcloud run deploy crm --source . --region southamerica-east1 --allow-unauthenticated \
      --set-env-vars SECRET_KEY=...,DATABASE_URL=postgresql://...,REGISTER_CODE=...,WA_TOKEN=...,WA_PHONE_ID=...,WA_VERIFY_TOKEN=...
Banco: crie uma instância Cloud SQL (PostgreSQL) e use a string de conexão em DATABASE_URL.

## WhatsApp
No painel Meta for Developers, configure o webhook: URL https://SEU-APP/webhook, token = WA_VERIFY_TOKEN, assine o campo "messages".
Regra da Meta: texto livre só dentro de 24h após a última mensagem do lead; campanhas exigem template aprovado.
