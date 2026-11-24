"""Cliente minimo da API do Chatwoot (mesmo padrao do TARZAP e do Calc Machine v3)."""

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("atendente.chatwoot")

# message_type no Chatwoot: 0 = incoming (cliente), 1 = outgoing (equipe/robo),
# 2 = activity (eventos do sistema), 3 = template.
INCOMING, OUTGOING, ACTIVITY = 0, 1, 2


@dataclass
class Mensagem:
    autor: str  # "cliente", "equipe" ou "nota_interna"
    texto: str


class Chatwoot:
    def __init__(self, url: str, conta: int, token: str, http: httpx.AsyncClient | None = None):
        self.raiz = f"{url}/api/v1"
        self.base = f"{self.raiz}/accounts/{conta}"
        self.token = token
        self.http = http or httpx.AsyncClient(timeout=20)

    async def perfil(self) -> dict:
        """Autoteste do token: quem e o dono dele no Chatwoot. Nunca devolve o token."""
        try:
            resp = await self.http.get(f"{self.raiz}/profile", headers={"api_access_token": self.token})
        except httpx.HTTPError as e:
            return {"ok": False, "erro": f"sem conexao com o Chatwoot: {type(e).__name__}"}
        if resp.status_code != 200:
            return {"ok": False, "http": resp.status_code}
        try:
            return {"ok": True, "http": 200, "nome": resp.json().get("name")}
        except ValueError:
            return {"ok": False, "http": 200, "erro": "resposta que nao e JSON"}

    async def _chamar(self, metodo: str, caminho: str, **kw) -> dict:
        resp = await self.http.request(
            metodo,
            f"{self.base}{caminho}",
            headers={"api_access_token": self.token, "Content-Type": "application/json"},
            **kw,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def mensagens(self, conversa_id: int, limite: int) -> list[Mensagem]:
        """Mensagens da conversa em ordem cronologica, sem eventos de sistema."""
        dados = await self._chamar("GET", f"/conversations/{conversa_id}/messages")
        brutas = sorted(dados.get("payload") or [], key=lambda m: m.get("created_at") or 0)
        saida: list[Mensagem] = []
        for m in brutas:
            texto = (m.get("content") or "").strip()
            tipo = m.get("message_type")
            if not texto or tipo not in (INCOMING, OUTGOING):
                continue
            if m.get("private"):
                autor = "nota_interna"
            elif tipo == INCOMING:
                autor = "cliente"
            else:
                autor = "equipe"
            saida.append(Mensagem(autor=autor, texto=texto))
        return saida[-limite:]

    async def enviar(self, conversa_id: int, texto: str, privada: bool) -> None:
        await self._chamar(
            "POST",
            f"/conversations/{conversa_id}/messages",
            json={"content": texto, "message_type": "outgoing", "private": privada},
        )

    async def atribuir(self, conversa_id: int, agente_id: int) -> None:
        await self._chamar("POST", f"/conversations/{conversa_id}/assignments", json={"assignee_id": agente_id})

    async def abrir_para_equipe(self, conversa_id: int) -> None:
        """Garante que a conversa fique 'aberta' (visivel na fila da equipe)."""
        await self._chamar("POST", f"/conversations/{conversa_id}/toggle_status", json={"status": "open"})
