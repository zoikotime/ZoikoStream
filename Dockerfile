# One image, one Cloud Run service: FastAPI serves the API and the built SPA from the same
# origin (server/app/main.py mounts client/dist). Two services would mean CORS config, two
# deploys and a second URL to keep in sync — none of which this app needs.

FROM node:22-alpine AS client
WORKDIR /client
COPY client/package*.json ./
RUN npm ci
COPY client/ ./
# No VITE_API_URL here on purpose: unset means the SPA calls its own origin at runtime, so
# the image works on any Cloud Run URL / custom domain without a rebuild.
RUN npm run build


FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY --from=client /client/dist ./client/dist

WORKDIR /app/server
# ponytail: one worker. main.py's lifespan starts the scheduler/sampler tickers per process,
# so N workers write N copies of every analytics snapshot and fire scheduled polls N times.
# Scale with Cloud Run instances, not workers. Cloud Run injects $PORT.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
