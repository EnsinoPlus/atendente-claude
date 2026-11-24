"""Regras do atendimento: quais eventos tratar, espera, limite e o que publicar no Chatwoot."""

import asyncio
import logging
import time
from collections import defaultdict, deque

from .cerebro import Cerebro, Decisao
from .chatwoot import Chatwoot
from .config import Config
from .planos import Planos

log = logging.getLogger("atendente")

PREFIXO_SUGESTAO = "🤖 Sugestão do atendente virtual (o cliente NÃO viu):\n\n"
PREFIXO_HUMANO = "🤖 Atendente virtual: este cliente precisa de uma pessoa da equipe.\nMotivo: "


def evento_relevante(ev: dict, cfg: Config) -> int | None:
    """Devolve o id da conversa se o evento for uma mensagem nova do cliente numa caixa atendida."""
    if ev.get("event") != "message_created":
        return None
    if ev.get("message_type") not in ("incoming", 0):
        return None
    if ev.get("private"):
        return None
    conversa = ev.get("conversation") or {}
    inbox_id = (ev.get("inbox") or {}).get("id") or conversa.get("inbox_id")
    if inbox_id not in cfg.inbox_ids:
        return None
    if conversa.get("status") == "resolved":
        return None
    return conversa.get("id")


def tem_atendente_humano(ev: dict) -> bool:
    meta = (ev.get("conversation") or {}).get("meta") or {}
    responsavel = meta.get("assignee") or {}
    return bool(responsavel) and responsavel.get("type", "user") == "user"


class Atendente:
    def __init__(self, cfg: Config, chatwoot: Chatwoot, cerebro: Cerebro, planos: Planos, relogio=time.monotonic):
        self.cfg = cfg
        self.chatwoot = chatwoot
        self.cerebro = cerebro
        self.planos = planos
        self.relogio = relogio
        self._pendentes: dict[int, asyncio.Task] = {}
        self._respostas: dict[int, deque] = defaultdict(deque)

    def receber(self, ev: dict) -> bool:
        """Chamado pelo webhook. Agenda a resposta e devolve na hora."""
        if self.cfg.modo == "desligado":
            return False
        conversa_id = evento_relevante(ev, self.cfg)
        if conversa_id is None:
            return False
        if self.cfg.modo == "automatico" and tem_atendente_humano(ev):
            # Uma pessoa assumiu a conversa: o robo nao fala por cima dela.
            return False
        anterior = self._pendentes.get(conversa_id)
        if anterior and not anterior.done():
            anterior.cancel()  # cliente mandou outra mensagem: recomeca a espera
        self._pendentes[conversa_id] = asyncio.create_task(self._depois_da_espera(conversa_id))
        return True

    async def _depois_da_espera(self, conversa_id: int) -> None:
        try:
            await asyncio.sleep(self.cfg.espera_segundos)
            await self.atender(conversa_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("falha ao atender a conversa %s", conversa_id)
        finally:
            if self._pendentes.get(conversa_id) is asyncio.current_task():
                self._pendentes.pop(conversa_id, None)

    def _dentro_do_limite(self, conversa_id: int) -> bool:
        agora = self.relogio()
        fila = self._respostas[conversa_id]
        while fila and agora - fila[0] > 3600:
            fila.popleft()
        if len(fila) >= self.cfg.max_respostas_por_hora:
            return False
        fila.append(agora)
        return True

    async def atender(self, conversa_id: int) -> Decisao | None:
        mensagens = await self.chatwoot.mensagens(conversa_id, self.cfg.historico_max_mensagens)
        if not mensagens or mensagens[-1].autor != "cliente":
            return None  # alguem ja respondeu durante a espera
        if self.cfg.modo == "automatico" and any(
            m.autor == "nota_interna" and m.texto.startswith(PREFIXO_HUMANO) for m in mensagens
        ):
            return None  # conversa ja transferida para a equipe: o robo nao volta a falar
        if not self._dentro_do_limite(conversa_id):
            log.warning("conversa %s atingiu o limite de respostas por hora", conversa_id)
            return None
        decisao = await self.cerebro.decidir(mensagens, await self.planos.texto())
        log.info("conversa %s: %s (%s)", conversa_id, decisao.acao, decisao.motivo)
        await self.publicar(conversa_id, decisao)
        return decisao

    async def publicar(self, conversa_id: int, d: Decisao) -> None:
        if d.acao == "nao_responder":
            return
        if d.acao == "responder" and not d.resposta:
            # Resposta vazia nao e uma resposta: na duvida, transfere.
            d = Decisao("passar_para_humano", "", d.motivo or "Resposta automática vazia.")
        if self.cfg.modo == "sugestao":
            if d.acao == "passar_para_humano":
                nota = (PREFIXO_HUMANO + (d.motivo or "não informado")
                        + "\n\nSugestão de mensagem ao cliente:\n" + self.cfg.mensagem_transferencia)
            else:
                nota = PREFIXO_SUGESTAO + d.resposta
            await self.chatwoot.enviar(conversa_id, nota, privada=True)
            return
        # modo automatico
        if d.acao == "responder":
            await self.chatwoot.enviar(conversa_id, d.resposta, privada=False)
            return
        # passar_para_humano: mensagem fixa (nunca texto do modelo), nota, atribuicao e fila aberta
        await self.chatwoot.enviar(conversa_id, self.cfg.mensagem_transferencia, privada=False)
        await self.chatwoot.enviar(conversa_id, PREFIXO_HUMANO + (d.motivo or "não informado"), privada=True)
        if self.cfg.atendente_humano_id:
            await self.chatwoot.atribuir(conversa_id, self.cfg.atendente_humano_id)
        await self.chatwoot.abrir_para_equipe(conversa_id)
