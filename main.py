from fastapi import FastAPI, Request

from bot.handlers import process_update


app = FastAPI(title="PMMP Daily Summary Bot", version="1.0.0")


@app.get("/")
async def healthcheck():
    return {"ok": True, "service": "pmmp-daily-summary-bot"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    process_update(update)
    return {"ok": True}
