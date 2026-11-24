"""Servidor HTTP: recebe o webhook do Chatwoot e entrega ao Atendente."""

import hmac
import logging
import os

from fastapi import FastAPI, Request, Response

from . import config
from .atendente import Atendente
from .cerebro import Cerebro
from .chatwoot import Chatwoot
from .planos import Planos

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("atendente.main")

CFG = config.carregar()
app = FastAPI(title="Atendente EnsinoPlus")
_atendente: Atendente | None = None


def atendente() -> Atendente:
    global _atendente
    if _atendente is None:
        _atendente = Atendente(
            CFG,
            Chatwoot(CFG.chatwoot_url, CFG.chatwoot_account_id, CFG.chatwoot_api_token),
            Cerebro(CFG.modelo, CFG.esforco),
            Planos(CFG.planos_url),
        )
    return _atendente


@app.get("/saude")
async def saude() -> dict:
    return {"ok": True, "modo": CFG.modo, "caixas": sorted(CFG.inbox_ids)}


@app.post("/webhooks/chatwoot/{segredo}")
async def webhook(segredo: str, request: Request) -> Response:
    # Segredo no caminho, comparado em tempo constante (o Chatwoot nao assina o corpo).
    if not CFG.webhook_segredo or not hmac.compare_digest(segredo, CFG.webhook_segredo):
        return Response(status_code=403)
    try:
        ev = await request.json()
        atendente().receber(ev)
    except Exception:
        log.exception("erro ao receber evento")
    return Response(status_code=200)
