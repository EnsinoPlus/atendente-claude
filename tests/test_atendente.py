"""Testes sem rede: Chatwoot, Claude e planos sao substituidos por dubles."""

import asyncio
import json
import unittest

import httpx

from app.atendente import PREFIXO_HUMANO, PREFIXO_SUGESTAO, Atendente, evento_relevante
from app.cerebro import Decisao, interpretar, montar_entrada
from app.chatwoot import Chatwoot, Mensagem
from app.config import MENSAGEM_TRANSFERENCIA, Config
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
        self.atribuidas: list[tuple[int, int]] = []
        self.lidas = 0
        self.falha_ao_atribuir = None

    async def atribuir(self, conversa_id, agente_id):
        if self.falha_ao_atribuir:
            raise self.falha_ao_atribuir
        self.atribuidas.append((conversa_id, agente_id))

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

    async def test_transferencia_no_automatico_usa_mensagem_fixa_e_atribui_ao_almir(self):
        d = Decisao("passar_para_humano", "texto inventado pelo modelo", "pediu desconto")
        a = atendente(cfg(modo="automatico", atendente_humano_id=3), [Mensagem("cliente", "Tem desconto?")], d)
        await a.atender(7)
        self.assertEqual(a.chatwoot.enviadas[0], (7, MENSAGEM_TRANSFERENCIA, False))
        self.assertIn("Almir Sousa", a.chatwoot.enviadas[0][1])
        self.assertEqual(a.chatwoot.enviadas[1], (7, PREFIXO_HUMANO + "pediu desconto", True))
        self.assertEqual(a.chatwoot.atribuidas, [(7, 3)])
        self.assertEqual(a.chatwoot.abertas, [7])

    async def test_transferencia_sem_id_do_almir_nao_atribui(self):
        d = Decisao("passar_para_humano", "", "dúvida fora da base")
        a = atendente(cfg(modo="automatico"), [Mensagem("cliente", "Vocês têm curso presencial?")], d)
        await a.atender(7)
        self.assertEqual(a.chatwoot.atribuidas, [])
        self.assertEqual(a.chatwoot.abertas, [7])

    async def test_falha_ao_atribuir_vira_nota_e_conversa_fica_aberta(self):
        d = Decisao("passar_para_humano", "", "pediu desconto")
        a = atendente(cfg(modo="automatico", atendente_humano_id=99), [Mensagem("cliente", "Tem desconto?")], d)
        a.chatwoot.falha_ao_atribuir = ValueError("agente inexistente")
        with self.assertLogs("atendente", "ERROR"):
            await a.atender(7)
        self.assertEqual(a.chatwoot.enviadas[0], (7, MENSAGEM_TRANSFERENCIA, False))
        cid, nota, privada = a.chatwoot.enviadas[2]
        self.assertTrue(privada)
        self.assertIn("não consegui atribuir", nota)
        self.assertEqual(a.chatwoot.abertas, [7])

    async def test_robo_nao_volta_a_falar_em_conversa_transferida(self):
        msgs = [Mensagem("cliente", "Tem desconto?"), Mensagem("equipe", MENSAGEM_TRANSFERENCIA),
                Mensagem("nota_interna", PREFIXO_HUMANO + "pediu desconto"), Mensagem("cliente", "Alô?")]
        a = atendente(cfg(modo="automatico"), msgs)
        self.assertIsNone(await a.atender(7))
        self.assertEqual(a.cerebro.chamadas, 0)

    async def test_resposta_vazia_vira_transferencia(self):
        a = atendente(cfg(modo="automatico"), [Mensagem("cliente", "Oi")], Decisao("responder", "", "?"))
        await a.atender(7)
        self.assertEqual(a.chatwoot.enviadas[0], (7, MENSAGEM_TRANSFERENCIA, False))

    async def test_transferencia_no_modo_sugestao_nao_fala_com_cliente(self):
        d = Decisao("passar_para_humano", "", "problema de pagamento")
        a = atendente(cfg(), [Mensagem("cliente", "Fui cobrado duas vezes")], d)
        await a.atender(7)
        self.assertEqual(len(a.chatwoot.enviadas), 1)
        cid, nota, privada = a.chatwoot.enviadas[0]
        self.assertTrue(privada)
        self.assertIn(MENSAGEM_TRANSFERENCIA, nota)
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

    def test_base_de_conhecimento_exige_certeza_e_transfere_para_almir(self):
        from app.cerebro import CONHECIMENTO
        self.assertIn("100% de certeza", CONHECIMENTO)
        self.assertIn("Almir Sousa", CONHECIMENTO)
        self.assertIn("Seu nome é **Atendente EnsinoPlus**", CONHECIMENTO)
        self.assertIn("Não se apresente nem diga seu nome por conta própria", CONHECIMENTO)
        self.assertIn("Atendente EnsinoPlus", PREFIXO_SUGESTAO)

    def test_entrada_sem_planos_proibe_citar_valor(self):
        texto = montar_entrada([Mensagem("cliente", "Quanto custa?")], None)
        self.assertIn("NÃO cite valores", texto)
        self.assertIn("[CLIENTE] Quanto custa?", texto)

    def test_entrada_informa_o_cumprimento_do_horario_de_brasilia(self):
        from datetime import datetime
        from app.cerebro import BRASILIA
        casos = {(4, 59): "Boa noite", (5, 0): "Bom dia", (11, 59): "Bom dia", (12, 0): "Boa tarde",
                 (17, 59): "Boa tarde", (18, 0): "Boa noite", (0, 30): "Boa noite"}
        for (h, m), esperado in casos.items():
            agora = datetime(2026, 11, 24, h, m, tzinfo=BRASILIA)
            texto = montar_entrada([Mensagem("cliente", "oi")], None, agora=agora)
            self.assertIn(f"Agora em Brasília: {h:02d}:{m:02d}. Cumprimento deste horário: {esperado}.", texto)

    def test_base_orienta_a_cumprimentar_com_cortesia(self):
        from app.cerebro import CONHECIMENTO
        self.assertIn("Seja cortês nos cumprimentos", CONHECIMENTO)
        self.assertIn("mesmo cumprimento", CONHECIMENTO)

    def test_extrai_json_de_bloco_de_codigo_e_de_texto_em_volta(self):
        puro = '{"acao": "responder", "resposta": "Oi", "motivo": "x"}'
        self.assertEqual(interpretar("```json\n" + puro + "\n```").resposta, "Oi")
        self.assertEqual(interpretar("Claro! " + puro + " Pronto.").resposta, "Oi")

    def test_campos_com_tipo_errado_nao_quebram(self):
        d = interpretar(json.dumps({"acao": "responder", "resposta": 123, "motivo": None}))
        self.assertEqual((d.acao, d.resposta, d.motivo), ("responder", "", ""))


class ExecutorFalso:
    """Substitui o subprocesso do Claude Code e guarda o que foi pedido."""

    def __init__(self, retorno=None, erro=None):
        self.retorno, self.erro, self.chamadas = retorno, erro, []

    async def __call__(self, args, entrada, env, cwd, timeout):
        self.chamadas.append({"args": args, "entrada": entrada, "env": env, "cwd": cwd, "timeout": timeout})
        if self.erro:
            raise self.erro
        return self.retorno


def envelope(resultado, **extra):
    return (0, json.dumps({"type": "result", "result": resultado, **extra}), "")


class CerebroClaudeCode(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import os
        from unittest import mock
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "token-de-teste",
                                                "ANTHROPIC_API_KEY": "chave-que-nao-deve-passar",
                                                "ANTHROPIC_AUTH_TOKEN": "outro-que-nao-deve-passar"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def cerebro(self, executor):
        from app.cerebro import Cerebro as CerebroReal
        return CerebroReal(cfg(modelo="sonnet"), executar=executor)

    async def test_responde_quando_o_claude_code_devolve_json_valido(self):
        ex = ExecutorFalso(envelope('{"acao": "responder", "resposta": "Olá!", "motivo": "saudação"}'))
        d = await self.cerebro(ex).decidir([Mensagem("cliente", "Oi")], "- plano X")
        self.assertEqual(d, Decisao("responder", "Olá!", "saudação"))

    async def test_chamada_sem_ferramentas_com_conhecimento_e_conversa_na_entrada(self):
        ex = ExecutorFalso(envelope('{"acao": "nao_responder", "resposta": "", "motivo": "x"}'))
        await self.cerebro(ex).decidir([Mensagem("cliente", "Quanto custa?")], "- plano X")
        [c] = ex.chamadas
        args = c["args"]
        self.assertEqual(args[:2], ["claude", "-p"])
        self.assertEqual(args[args.index("--tools") + 1], "")  # nenhuma ferramenta
        self.assertIn("--system-prompt-file", args)
        self.assertIn("--no-session-persistence", args)
        self.assertEqual(args[args.index("--output-format") + 1], "json")
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertNotIn("--bare", args)  # --bare nao le o login da assinatura
        self.assertIn("[CLIENTE] Quanto custa?", c["entrada"])
        self.assertIn("- plano X", c["entrada"])

    async def test_ambiente_usa_so_o_token_da_assinatura(self):
        ex = ExecutorFalso(envelope('{"acao": "nao_responder", "resposta": "", "motivo": "x"}'))
        await self.cerebro(ex).decidir([Mensagem("cliente", "Oi")], None)
        env = ex.chamadas[0]["env"]
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "token-de-teste")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertEqual(env["DISABLE_UPDATES"], "1")

    async def test_sem_token_nao_chama_o_claude_e_transfere(self):
        import os
        from unittest import mock
        ex = ExecutorFalso(envelope("{}"))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": ""}):
            d = await self.cerebro(ex).decidir([Mensagem("cliente", "Oi")], None)
        self.assertEqual(d.acao, "passar_para_humano")
        self.assertEqual(ex.chamadas, [])

    async def test_falhas_viram_transferencia(self):
        casos = {
            "codigo diferente de zero": ExecutorFalso((1, "Not logged in", "")),
            "saida que nao e json": ExecutorFalso((0, "texto solto", "")),
            "is_error verdadeiro": ExecutorFalso(envelope("erro", is_error=True)),
            "resultado fora do formato": ExecutorFalso(envelope("Claro, posso ajudar!")),
            "tempo esgotado": ExecutorFalso(erro=asyncio.TimeoutError()),
            "claude nao instalado": ExecutorFalso(erro=FileNotFoundError()),
        }
        for nome, ex in casos.items():
            with self.subTest(nome):
                d = await self.cerebro(ex).decidir([Mensagem("cliente", "Oi")], None)
                self.assertEqual(d.acao, "passar_para_humano")
                self.assertEqual(d.resposta, "")


class Autoteste(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import os
        from unittest import mock
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "token-de-teste"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def cerebro(self, executor):
        from app.cerebro import Cerebro as CerebroReal
        return CerebroReal(cfg(modelo="sonnet"), executar=executor)

    async def test_claude_ok(self):
        ex = ExecutorFalso(envelope("ok"))
        r = await self.cerebro(ex).testar()
        self.assertTrue(r["ok"])
        self.assertEqual(r["resposta"], "ok")
        self.assertEqual(ex.chamadas[0]["args"][ex.chamadas[0]["args"].index("--tools") + 1], "")
        self.assertEqual(ex.chamadas[1]["args"], ["claude", "auth", "status"])

    async def test_formato_do_token_so_mostra_sim_ou_nao_e_tamanho(self):
        import os
        from unittest import mock
        casos = {
            "sk-ant-oat01-" + "a" * 90: (103, True, False),
            "sk-ant-oat01-abc\ndef": (20, True, True),
            "qualquer-coisa": (14, False, False),
        }
        for valor, (tam, prefixo, quebra) in casos.items():
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": valor}):
                r = await self.cerebro(ExecutorFalso(envelope("ok"))).testar()
            f = r["token"]
            self.assertEqual((f["tamanho"], f["prefixo_sk_ant_oat01"], f["tem_espaco_ou_quebra_de_linha"]), (tam, prefixo, quebra))
            self.assertNotIn(valor, json.dumps(r))

    async def test_token_com_quebra_de_linha_no_meio_e_limpo_antes_de_usar(self):
        import os
        from unittest import mock
        ex = ExecutorFalso(envelope('{"acao": "nao_responder", "resposta": "", "motivo": "x"}'))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-abc\r\n  def\n"}):
            await self.cerebro(ex).decidir([Mensagem("cliente", "Oi")], None)
            r = await self.cerebro(ExecutorFalso(envelope("ok"))).testar()
        self.assertEqual(ex.chamadas[0]["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-abcdef")
        self.assertEqual(r["token"]["tamanho_sem_espacos"], 19)
        self.assertTrue(r["token"]["tem_espaco_ou_quebra_de_linha"])

    async def test_auth_status_mostra_so_campos_seguros(self):
        saida = json.dumps({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty", "email": "x@y"})
        r = await self.cerebro(ExecutorFalso((0, saida, ""))).testar()
        self.assertEqual(r["auth_status"], {"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"})

    async def test_claude_falhas_viram_ok_falso_sem_expor_o_token(self):
        casos = [ExecutorFalso((1, "Invalid bearer token", "")), ExecutorFalso((0, "texto", "")),
                 ExecutorFalso(envelope("erro", is_error=True)), ExecutorFalso(erro=asyncio.TimeoutError()),
                 ExecutorFalso(erro=FileNotFoundError())]
        for ex in casos:
            r = await self.cerebro(ex).testar()
            self.assertFalse(r["ok"])
            self.assertNotIn("token-de-teste", json.dumps(r))

    async def test_erro_do_claude_mostra_o_campo_result_do_json(self):
        saida = json.dumps({"type": "result", "is_error": True, "result": "Invalid API key", "usage": {"x": 1}})
        r = await self.cerebro(ExecutorFalso((1, saida, ""))).testar()
        self.assertFalse(r["ok"])
        self.assertEqual((r["codigo"], r["erro"]), (1, "Invalid API key"))

    async def test_chatwoot_perfil_devolve_o_nome_do_dono_do_token(self):
        def responde(req):
            self.assertEqual(str(req.url), "https://cw/api/v1/profile")
            return httpx.Response(200, json={"name": "Atendente EnsinoPlus", "access_token": "nao-deve-sair"})
        c = Chatwoot("https://cw", 1, "tok", http=httpx.AsyncClient(transport=httpx.MockTransport(responde)))
        r = await c.perfil()
        self.assertEqual(r, {"ok": True, "http": 200, "nome": "Atendente EnsinoPlus"})

    async def test_chatwoot_perfil_token_invalido(self):
        c = Chatwoot("https://cw", 1, "ruim", http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
        self.assertEqual(await c.perfil(), {"ok": False, "http": 401})

    async def test_chatwoot_perfil_sem_conexao(self):
        def falha(req):
            raise httpx.ConnectError("sem rede")
        c = Chatwoot("https://cw", 1, "tok", http=httpx.AsyncClient(transport=httpx.MockTransport(falha)))
        self.assertFalse((await c.perfil())["ok"])


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

    async def test_atribuir_envia_assignee_id(self):
        vistos = {}

        def responde(req):
            vistos["url"], vistos["corpo"] = str(req.url), json.loads(req.content)
            return httpx.Response(200, json={"id": 3, "name": "Almir Sousa"})
        c = Chatwoot("https://cw", 1, "tok", http=httpx.AsyncClient(transport=httpx.MockTransport(responde)))
        await c.atribuir(7, 3)
        self.assertEqual(vistos["url"], "https://cw/api/v1/accounts/1/conversations/7/assignments")
        self.assertEqual(vistos["corpo"], {"assignee_id": 3})

    async def test_atribuir_com_id_inexistente_falha(self):
        # O Chatwoot responde 200 com corpo "null" quando o agente nao existe na conta.
        c = Chatwoot("https://cw", 1, "tok", http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"null"))))
        with self.assertRaises(ValueError):
            await c.atribuir(7, 99)

    def _cw_agentes(self, membros):
        def responde(req):
            if req.url.path.endswith("/agents"):
                return httpx.Response(200, json=[{"id": 3, "name": "Almir Sousa", "email": "nao-deve-sair"},
                                                  {"id": 5, "name": "Atendente EnsinoPlus"}])
            return httpx.Response(200, json={"payload": [{"id": i} for i in membros]})
        return Chatwoot("https://cw", 1, "tok", http=httpx.AsyncClient(transport=httpx.MockTransport(responde)))

    async def test_conferir_agente_ok(self):
        r = await self._cw_agentes([3, 5]).conferir_agente(3, {11})
        self.assertEqual(r, {"ok": True, "id": 3, "nome": "Almir Sousa"})

    async def test_conferir_agente_inexistente_lista_os_agentes_sem_email(self):
        r = await self._cw_agentes([3]).conferir_agente(99, {11})
        self.assertFalse(r["ok"])
        self.assertEqual(r["agentes"], [{"id": 3, "nome": "Almir Sousa"}, {"id": 5, "nome": "Atendente EnsinoPlus"}])
        self.assertNotIn("nao-deve-sair", json.dumps(r))

    async def test_conferir_agente_vazio_e_fora_da_caixa(self):
        self.assertFalse((await self._cw_agentes([3]).conferir_agente(None, {11}))["ok"])
        r = await self._cw_agentes([5]).conferir_agente(3, {11})
        self.assertFalse(r["ok"])
        self.assertIn("[11]", r["erro"])


if __name__ == "__main__":
    unittest.main()
