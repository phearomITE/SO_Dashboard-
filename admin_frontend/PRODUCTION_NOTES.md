# SO Alert Bot Admin Frontend

The production dashboard uses this React UI inside the root FastAPI image. The
root Compose stack supplies the authenticated, PostgreSQL-backed API. Use the
project-level `README.md` and `ADMIN_DASHBOARD_SETUP.md` for that deployment.

## Run locally

```sh
npm ci
npm run dev
```

Running Vite directly serves only this frontend; it does not start FastAPI or
proxy API requests. The API-backed pages require the root dashboard service,
and an unavailable API must be treated as an error rather than as real data.

## Optional ngrok tunnel

This file defines a separate static preview stack. It is not used by
`RUN_NGROK_DASHBOARD.bat` and does not include the production FastAPI API or
PostgreSQL connection. Its extra ngrok Basic Auth requirement remains scoped
to that preview stack.

1. Revoke the exposed ngrok token in the ngrok dashboard and create a replacement.
2. Copy `.env.ngrok.example` to `.env.ngrok`; set the new token and a strong Basic Auth value formatted `username:password`.
3. Keep `.env.ngrok` private; it is excluded from Git.
4. Run `docker compose --env-file .env.ngrok -f compose.ngrok.yaml up --build -d`.
5. Read the assigned public URL from `docker compose --env-file .env.ngrok -f compose.ngrok.yaml logs ngrok`.
6. Stop the public tunnel when not needed with `docker compose --env-file .env.ngrok -f compose.ngrok.yaml down`.

The ngrok inspector is bound to `127.0.0.1:4040`, so it is not exposed publicly. Do not use this static preview as the SO production dashboard.
