# ZoikoStream API

FastAPI + SQLAlchemy auth backend (Supabase Postgres).

## Run

```bash
cd server
venv/Scripts/pip install -r requirements.txt   # first time
venv/Scripts/uvicorn app.main:app --reload --port 8000
```

Tables are auto-created on startup. Docs at http://localhost:8000/docs

## Env (`server/.env`)

- `DATABASE_URL` — Supabase Postgres connection string
- `SECRET_KEY` — JWT signing secret (generate: `python -c "import secrets;print(secrets.token_urlsafe(48))"`)

## Auth endpoints

| Method | Path                    | Purpose                                   |
|--------|-------------------------|-------------------------------------------|
| POST   | `/auth/register`        | Create org + first user (org_admin), returns token |
| POST   | `/auth/login`           | Login by username **or** email            |
| POST   | `/auth/forgot-password` | Issue reset token (dev: returned in body) |
| POST   | `/auth/reset-password`  | Set new password from token               |
| GET    | `/auth/me`              | Current user (requires `Bearer` token)    |

> Reset emails aren't wired yet — `forgot-password` returns `dev_reset_token` in the response.
> Wire an email provider and drop that field before launch.
