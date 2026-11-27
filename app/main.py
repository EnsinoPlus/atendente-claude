"""Servidor HTTP: recebe o webhook do Chatwoot e entrega ao Atendente."""

import hmac
import logging
import os

from fastapi import FastAPI, Request, Response

from . import config
from .atendente import Atendente
from .cerebro import Cerebro, claude_instalado, token_configurado
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
            Cerebro(CFG),
            Planos(CFG.planos_url),
        )
    return _atendente


def segredo_valido(segredo: str) -> bool:
    """Compara em tempo constante (o Chatwoot nao assina o corpo do webhook)."""
    return bool(CFG.webhook_segredo) and hmac.compare_digest(segredo.encode(), CFG.webhook_segredo.encode())


@app.get("/saude")
async def saude() -> dict:
    # Nunca mostra valores secretos. Os tamanhos ajudam a ver se um valor foi colado no lugar errado:
    # o segredo do webhook gerado tem 40 caracteres e e bem menor que os tokens.
    return {
        "ok": True,
        "modo": CFG.modo,
        "caixas": sorted(CFG.inbox_ids),
        # Id (nao secreto) do agente que recebe as transferencias. null = a conversa nao e atribuida.
        "atendente_humano_id": CFG.atendente_humano_id,
        "claude_instalado": claude_instalado(),
        "token_configurado": token_configurado(),
        "tamanhos": {
            "webhook_segredo": len(CFG.webhook_segredo),
            "chatwoot_token": len(CFG.chatwoot_api_token),
            "claude_token": len(os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()),
        },
    }


@app.get("/diagnostico/{segredo}", response_model=None)
async def diagnostico(segredo: str):
    """Autoteste protegido pelo segredo do webhook: o token do Chatwoot e o login do Claude Code funcionam?"""
    if not segredo_valido(segredo):
        return Response(status_code=403)
    a = atendente()
    return {
        "chatwoot": await a.chatwoot.perfil(),
        "atendente_humano": await a.chatwoot.conferir_agente(CFG.atendente_humano_id, CFG.inbox_ids),
        "claude": await a.cerebro.testar(),
    }


@app.post("/webhooks/chatwoot/{segredo}")
async def webhook(segredo: str, request: Request) -> Response:
    if not segredo_valido(segredo):
        return Response(status_code=403)
    try:
        ev = await request.json()
        atendente().receber(ev)
    except Exception:
        log.exception("erro ao receber evento")
    return Response(status_code=200)
