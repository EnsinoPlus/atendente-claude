"""Testes sem rede: Chatwoot, Claude e planos sao substituidos por dubles."""

import asyncio
import json
import unittest

import httpx

from app.atendente import PREFIXO_HUMANO, PREFIXO_SUGESTAO, Atendente, evento_relevante
from app.cerebro import Decisao, interpretar, montar_entrada
from app.chatwoot import Chatwoot, Mensagem
from app.config import Config
from app.planos import Planos, formatar

PLANOS_JSON = {
    "success": True,
    "checkout_url": "https://assinaturas.ensinoplus.com.br/assinar",
    "teste_gratis": {"disponivel": True, "dias": 2, "como": "Clique em Testar gratis.", "link": "https://assinaturas.ensinoplus.com.br"},
    "plans": [
        {"name": "CALC MACHINE CONNECT - MENSAL", "duration_days": 30, "price_brl": 99.7,
         "formas_disponiveis": ["pix_recorrente", "card"], "renovam_sozinhas": ["pix_recorrente", "card"],
         "link": "https://assinaturas.ensinoplus.com.br/assinar?plano=5"},
        {"name": "CALC MACHINE CONNECT - ANUAL", "duration_days": 365, "price_brl": 897,
         "formas_disponiveis": ["pix_recorrente", "card"], "renovam_sozinhas": ["pix_recorrente"],
         "cartao_parcelas_max": 10, "cartao_sem_juros": True,
         "link": "https://assinaturas.ensinoplus.com.br/assinar?plano=7"},
    ],
}


def cfg(**kw) -> Config:
    base = dict(modo="sugestao", inbox_ids=frozenset({11}), espera_segundos=0.01, max_respostas_por_hora=2)
    base.update(kw)
    return Config(**base)


def evento(conversa=4136, inbox=11, tipo="incoming", privada=False, status="pending", responsavel=None):
    return {
        "event": "message_created", "message_type": tipo, "private": privada, "content": "oi",
        "inbox": {"id": inbox},
        "conversation": {"id": conversa, "inbox_id": inbox, "status": status,
                         "meta": {"assignee": responsavel} if responsavel else {}},
    }


class ChatwootFalso:
    def __init__(self, mensagens):
        self.lista = mensagens
        self.enviadas: list[tuple[int, str, bool]] = []
        self.abertas: list[int] = []
        self.lidas = 0

    async def mensagens(self, conversa_id, limite):
        self.lidas += 1
        return self.lista[-limite:]

    async def enviar(self, conversa_id, texto, privada):
        self.enviadas.append((conversa_id, texto, privada))

    async def abrir_para_equipe(self, conversa_id):
        self.abertas.append(conversa_id)


class CerebroFalso:
    def __init__(self, decisao):
        self.decisao = decisao
        self.chamadas = 0

    async def decidir(self, mensagens, planos):
        self.chamadas += 1
        return self.decisao


class PlanosFalso:
    async def texto(self):
        return "- plano X"


def atendente(c: Config, msgs, decisao=Decisao("responder", "Olá!", "dúvida simples")):
    return Atendente(c, ChatwootFalso(msgs), CerebroFalso(decisao), PlanosFalso())


class FiltroDeEventos(unittest.TestCase):
    def test_mensagem_do_cliente_na_caixa_atendida(self):
        self.assertEqual(evento_relevante(evento(), cfg()), 4136)

    def test_ignora_mensagem_da_equipe_nota_outra_caixa_e_resolvida(self):
        self.assertIsNone(evento_relevante(evento(tipo="outgoing"), cfg()))
        self.assertIsNone(evento_relevante(evento(privada=True), cfg()))
        self.assertIsNone(evento_relevante(evento(inbox=99), cfg()))
        self.assertIsNone(evento_relevante(evento(status="resolved"), cfg()))
        self.assertIsNone(evento_relevante({**evento(), "event": "conversation_created"}, cfg()))


class Publicacao(unittest.IsolatedAsyncioTestCase):
    async def test_modo_sugestao_escreve_nota_interna(self):
        a = atendente(cfg(), [Mensagem("cliente", "Cada crédito é um cálculo?")])
        await a.atender(4136)
        [(cid, texto, privada)] = a.chatwoot.enviadas
        self.assertTrue(privada)
        self.assertTrue(texto.startswith(PREFIXO_SUGESTAO))
        self.assertIn("Olá!", texto)

    async def test_modo_automatico_responde_ao_cliente(self):
        a = atendente(cfg(modo="automatico"), [Mensagem("cliente", "Oi")])
        await a.atender(1)
        self.assertEqual(a.chatwoot.enviadas, [(1, "Olá!", False)])

    async def test_passar_para_humano_no_automatico(self):
        d = Decisao("passar_para_humano", "Vou chamar alguém da equipe.", "pediu desconto")
        a = atendente(cfg(modo="automatico"), [Mensagem("cliente", "Tem desconto?")], d)
        await a.atender(7)
        self.assertEqual(a.chatwoot.enviadas[0], (7, "Vou chamar alguém da equipe.", False))
        self.assertEqual(a.chatwoot.enviadas[1], (7, PREFIXO_HUMANO + "pediu desconto", True))
        self.assertEqual(a.chatwoot.abertas, [7])

    async def test_passar_para_humano_no_modo_sugestao_nao_fala_com_cliente(self):
        d = Decisao("passar_para_humano", "Vou chamar alguém.", "problema de pagamento")
        a = atendente(cfg(), [Mensagem("cliente", "Fui cobrado duas vezes")], d)
        await a.atender(7)
        self.assertEqual(len(a.chatwoot.enviadas), 1)
        self.assertTrue(a.chatwoot.enviadas[0][2])
        self.assertEqual(a.chatwoot.abertas, [])

    async def test_nao_responde_se_equipe_ja_respondeu(self):
        a = atendente(cfg(), [Mensagem("cliente", "Oi"), Mensagem("equipe", "Olá, em que posso ajudar?")])
        self.assertIsNone(await a.atender(1))
        self.assertEqual(a.cerebro.chamadas, 0)

    async def test_nao_responder_nao_publica(self):
        a = atendente(cfg(), [Mensagem("cliente", "ok")], Decisao("nao_responder", "", "nada a acrescentar"))
        await a.atender(1)
        self.assertEqual(a.chatwoot.enviadas, [])

    async def test_limite_por_hora(self):
        a = atendente(cfg(max_respostas_por_hora=2), [Mensagem("cliente", "Oi")])
        for _ in range(3):
            await a.atender(1)
        self.assertEqual(a.cerebro.chamadas, 2)


class EsperaEntreMensagens(unittest.IsolatedAsyncioTestCase):
    async def test_junta_mensagens_seguidas_numa_resposta(self):
        a = atendente(cfg(espera_segundos=0.05), [Mensagem("cliente", "a"), Mensagem("cliente", "b")])
        self.assertTrue(a.receber(evento()))
        await asyncio.sleep(0.02)
        self.assertTrue(a.receber(evento()))  # segunda mensagem antes do fim da espera
        await asyncio.sleep(0.15)
        self.assertEqual(a.cerebro.chamadas, 1)

    async def test_desligado_nao_agenda(self):
        a = atendente(cfg(modo="desligado"), [Mensagem("cliente", "Oi")])
        self.assertFalse(a.receber(evento()))

    async def test_automatico_respeita_atendente_humano(self):
        a = atendente(cfg(modo="automatico"), [Mensagem("cliente", "Oi")])
        self.assertFalse(a.receber(evento(responsavel={"id": 1, "name": "Almir", "type": "user"})))

    async def test_sugestao_continua_mesmo_com_atendente_humano(self):
        a = atendente(cfg(), [Mensagem("cliente", "Oi")])
        self.assertTrue(a.receber(evento(responsavel={"id": 1, "name": "Almir", "type": "user"})))
        await asyncio.sleep(0.05)


class Cerebro(unittest.TestCase):
    def test_interpreta_json_valido(self):
        d = interpretar(json.dumps({"acao": "responder", "resposta": " Oi ", "motivo": "x"}))
        self.assertEqual(d, Decisao("responder", "Oi", "x"))

    def test_saida_invalida_vira_passar_para_humano(self):
        self.assertEqual(interpretar("não é json").acao, "passar_para_humano")
        self.assertEqual(interpretar(json.dumps({"acao": "vender"})).acao, "passar_para_humano")

    def test_entrada_sem_planos_proibe_citar_valor(self):
        texto = montar_entrada([Mensagem("cliente", "Quanto custa?")], None)
        self.assertIn("NÃO cite valores", texto)
        self.assertIn("[CLIENTE] Quanto custa?", texto)


class PlanosTeste(unittest.IsolatedAsyncioTestCase):
    def test_formata_precos_em_reais(self):
        t = formatar(PLANOS_JSON)
        self.assertIn("R$ 99,70 por 30 dias", t)
        self.assertIn("R$ 897,00 por 365 dias", t)
        self.assertIn("10x sem juros", t)
        self.assertIn("Teste gratis: 2 dias", t)

    async def test_falha_de_rede_sem_cache_devolve_none(self):
        def falha(req):
            raise httpx.ConnectError("sem rede")
        p = Planos("https://x/planos", http=httpx.AsyncClient(transport=httpx.MockTransport(falha)))
        self.assertIsNone(await p.texto())

    async def test_cache_mantem_ultimo_valor_bom(self):
        chamadas = {"n": 0}

        def responde(req):
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                return httpx.Response(200, json=PLANOS_JSON)
            return httpx.Response(500)
        agora = {"t": 0.0}
        p = Planos("https://x/planos", http=httpx.AsyncClient(transport=httpx.MockTransport(responde)), relogio=lambda: agora["t"])
        primeiro = await p.texto()
        agora["t"] = 10_000  # cache venceu, servidor falha
        self.assertEqual(await p.texto(), primeiro)


class ChatwootTeste(unittest.IsolatedAsyncioTestCase):
    async def test_le_mensagens_em_ordem_e_classifica_autor(self):
        payload = {"payload": [
            {"content": "resposta", "message_type": 1, "private": False, "created_at": 3},
            {"content": "pergunta", "message_type": 0, "private": False, "created_at": 1},
            {"content": "nota", "message_type": 1, "private": True, "created_at": 4},
            {"content": "Almir atribuiu", "message_type": 2, "private": False, "created_at": 2},
            {"content": "", "message_type": 0, "private": False, "created_at": 5},
        ]}
        vistos = {}

        def responde(req):
            vistos["url"] = str(req.url)
            vistos["token"] = req.headers.get("api_access_token")
            return httpx.Response(200, json=payload)
        c = Chatwoot("https://cw", 1, "tok", http=httpx.AsyncClient(transport=httpx.MockTransport(responde)))
        msgs = await c.mensagens(4136, 10)
        self.assertEqual([(m.autor, m.texto) for m in msgs],
                         [("cliente", "pergunta"), ("equipe", "resposta"), ("nota_interna", "nota")])
        self.assertEqual(vistos["url"], "https://cw/api/v1/accounts/1/conversations/4136/messages")
        self.assertEqual(vistos["token"], "tok")

    async def test_envia_nota_privada(self):
        corpo = {}

        def responde(req):
            corpo.update(json.loads(req.content))
            return httpx.Response(200, json={"id": 1})
        c = Chatwoot("https://cw", 1, "tok", http=httpx.AsyncClient(transport=httpx.MockTransport(responde)))
        await c.enviar(5, "texto", privada=True)
        self.assertEqual(corpo, {"content": "texto", "message_type": "outgoing", "private": True})


if __name__ == "__main__":
    unittest.main()
