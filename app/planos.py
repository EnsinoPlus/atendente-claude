"""Precos atuais do Calc Machine Connect, lidos da lista publica oficial.

Nunca usar precos fixos no prompt: eles mudam. Se a lista nao puder ser lida,
o robo e instruido a mandar o link de planos em vez de citar valores.
"""

import logging
import time

import httpx

log = logging.getLogger("atendente.planos")

_CACHE_SEGUNDOS = 600


def _brl(valor: float) -> str:
    inteiro, centavos = f"{valor:,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def formatar(dados: dict) -> str:
    linhas = []
    for p in dados.get("plans") or []:
        formas = ", ".join(p.get("formas_disponiveis") or [])
        renovam = ", ".join(p.get("renovam_sozinhas") or []) or "nenhuma"
        extra = ""
        if p.get("cartao_parcelas_max"):
            juros = "sem juros" if p.get("cartao_sem_juros") else "com juros"
            extra = f"; cartao em ate {p['cartao_parcelas_max']}x {juros}"
        linhas.append(
            f"- {p.get('name')}: {_brl(float(p.get('price_brl') or 0))} por {p.get('duration_days')} dias"
            f" (formas: {formas}; renovam sozinhas: {renovam}{extra}). Link: {p.get('link')}"
        )
    teste = dados.get("teste_gratis") or {}
    if teste.get("disponivel"):
        linhas.append(
            f"- Teste gratis: {teste.get('dias')} dias. {teste.get('como', '')} Link: {teste.get('link')}"
        )
    if dados.get("checkout_url"):
        linhas.append(f"- Pagina para escolher o plano: {dados['checkout_url']}")
    return "\n".join(linhas)


class Planos:
    def __init__(self, url: str, http: httpx.AsyncClient | None = None, relogio=time.monotonic):
        self.url = url
        self.http = http or httpx.AsyncClient(timeout=10)
        self.relogio = relogio
        self._texto: str | None = None
        self._lido_em = 0.0

    async def texto(self) -> str | None:
        agora = self.relogio()
        if self._texto is not None and agora - self._lido_em < _CACHE_SEGUNDOS:
            return self._texto
        try:
            resp = await self.http.get(self.url)
            resp.raise_for_status()
            dados = resp.json()
            if not dados.get("success"):
                raise ValueError("lista de planos sem success=true")
            self._texto = formatar(dados)
            self._lido_em = agora
        except Exception as e:  # rede, JSON, formato
            log.warning("nao foi possivel ler os planos: %s", e)
            # Mantem o ultimo valor bom se houver; senao, None (o robo nao cita preco).
        return self._texto
