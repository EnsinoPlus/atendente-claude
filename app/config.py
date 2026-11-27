"""Configuracao lida das variaveis de ambiente (definidas no Easypanel)."""

import os
from dataclasses import dataclass, field


MENSAGEM_TRANSFERENCIA = (
    "Para te dar uma resposta com total segurança, vou transferir seu atendimento "
    "para o atendente que estiver disponível. Só um instante, por favor."
)


def _lista_int(valor: str) -> frozenset[int]:
    itens = [v.strip() for v in (valor or "").split(",") if v.strip()]
    return frozenset(int(v) for v in itens)


@dataclass(frozen=True)
class Config:
    # sugestao  = escreve a resposta como nota interna (cliente nao ve); um humano envia.
    # automatico = responde direto ao cliente.
    # desligado = recebe os eventos e nao faz nada (interruptor).
    modo: str = "sugestao"

    chatwoot_url: str = ""
    chatwoot_account_id: int = 1
    chatwoot_api_token: str = ""
    webhook_segredo: str = ""
    # Caixas de entrada atendidas (ids do Chatwoot). Vazio = nenhuma.
    inbox_ids: frozenset[int] = field(default_factory=frozenset)

    # Modelo do Claude Code (alias como "sonnet" ou "opus", ou o nome completo do modelo).
    modelo: str = "sonnet"
    # Tempo maximo de uma consulta ao Claude Code e quantas rodam ao mesmo tempo.
    claude_timeout_segundos: float = 120.0
    claude_concorrencia: int = 1

    # Espera depois da ultima mensagem do cliente antes de responder,
    # para juntar mensagens enviadas em sequencia.
    espera_segundos: float = 20.0
    # Teto de respostas do robo por conversa a cada hora (protecao contra loop).
    max_respostas_por_hora: int = 8
    # Quantas mensagens recentes da conversa entram no contexto.
    historico_max_mensagens: int = 40

    planos_url: str = "https://assinaturas.ensinoplus.com.br/api/public/mcp/plans"
    suporte_whatsapp: str = "https://wa.me/5585996641461"

    # Mensagem fixa enviada ao cliente quando o robo nao tem certeza e transfere.
    mensagem_transferencia: str = MENSAGEM_TRANSFERENCIA
    # Id do agente "Almir Sousa" no Chatwoot. Se preenchido, a conversa e atribuida a ele ao transferir.
    atendente_humano_id: int | None = None


def carregar() -> Config:
    modo = os.getenv("MODO", "sugestao").strip().lower()
    if modo not in ("sugestao", "automatico", "desligado"):
        modo = "desligado"
    return Config(
        modo=modo,
        chatwoot_url=os.getenv("CHATWOOT_URL", "").rstrip("/"),
        chatwoot_account_id=int(os.getenv("CHATWOOT_ACCOUNT_ID", "1")),
        chatwoot_api_token=os.getenv("CHATWOOT_API_TOKEN", ""),
        webhook_segredo=os.getenv("WEBHOOK_SEGREDO", ""),
        inbox_ids=_lista_int(os.getenv("CHATWOOT_INBOX_IDS", "")),
        modelo=os.getenv("CLAUDE_MODELO", "sonnet"),
        claude_timeout_segundos=float(os.getenv("CLAUDE_TIMEOUT_SEGUNDOS", "120")),
        claude_concorrencia=max(1, int(os.getenv("CLAUDE_CONCORRENCIA", "1"))),
        espera_segundos=float(os.getenv("ESPERA_SEGUNDOS", "20")),
        max_respostas_por_hora=int(os.getenv("MAX_RESPOSTAS_POR_HORA", "8")),
        historico_max_mensagens=int(os.getenv("HISTORICO_MAX_MENSAGENS", "40")),
        planos_url=os.getenv("PLANOS_URL", "https://assinaturas.ensinoplus.com.br/api/public/mcp/plans"),
        suporte_whatsapp=os.getenv("SUPORTE_WHATSAPP", "https://wa.me/5585996641461"),
        mensagem_transferencia=os.getenv("MENSAGEM_TRANSFERENCIA", MENSAGEM_TRANSFERENCIA),
        atendente_humano_id=int(os.getenv("ATENDENTE_HUMANO_ID")) if os.getenv("ATENDENTE_HUMANO_ID") else None,
    )
