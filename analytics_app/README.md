# Token-inform Analytics (prototype)

Independent Telegram Mini App prototype. Does not modify the existing bot or trading accounts.

## Local run

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Run commands from `analytics_app/`. Visit http://localhost:8000.

## Coolify

Create a **separate** application from GitHub branch `test`, with **Base Directory** `/analytics_app` and Dockerfile build. Set port 8000 and HTTPS domain. Do not change the existing app.

## Telegram

Configure the HTTPS URL as the Mini App URL for the *new* bot in BotFather. Telegram user authorization and persistence are **not implemented yet**. Do not expose personal settings or private endpoints until server-side Telegram initData validation is implemented.

Public market endpoints: `/api/ticker?category=linear&symbol=BTCUSDT` and `/api/candles?category=spot&symbol=BTCUSDT&interval=15`.
