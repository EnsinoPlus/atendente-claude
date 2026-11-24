# Atendente EnsinoPlus — atendente virtual da Ensino Plus (Chatwoot + Claude)

Recebe as mensagens novas dos clientes no Chatwoot, consulta o Claude com a base de conhecimento
(`conhecimento/atendimento.md`) e os preços oficiais do momento, e:

- **modo `sugestao`** (padrão): escreve a resposta como **nota interna**. O cliente não vê; um atendente revisa e envia.
- **modo `automatico`**: responde direto ao cliente. Não responde em conversas atribuídas a um atendente humano.
- **modo `desligado`**: recebe os eventos e não faz nada (interruptor de emergência).

**Regra principal:** o robô só responde o que está escrito na base de conhecimento ou na lista oficial de preços.
Na menor dúvida, transfere. No modo automático, a transferência envia ao cliente uma **mensagem fixa**
(nunca escrita pelo modelo): "Para te dar uma resposta com total segurança, vou transferir seu atendimento para o
atendente Almir Sousa. Só um instante, por favor." Depois deixa nota interna com o motivo, atribui a conversa ao
Almir (se `ATENDENTE_HUMANO_ID` estiver preenchido), deixa a conversa aberta e não volta a falar nela.

## Como funciona

1. O Chatwoot chama `POST /webhooks/chatwoot/<WEBHOOK_SEGREDO>` a cada mensagem criada.
2. Só conta mensagem **do cliente**, **não privada**, numa caixa de `CHATWOOT_INBOX_IDS`, em conversa não resolvida.
3. Espera `ESPERA_SEGUNDOS` (20 s) sem mensagem nova do cliente, para juntar mensagens seguidas numa resposta só.
4. Relê a conversa. Se a última mensagem já não for do cliente (alguém respondeu), não faz nada.
5. Consulta os preços em `https://assinaturas.ensinoplus.com.br/api/public/mcp/plans` (cache de 10 min).
   Se não conseguir, o robô é proibido de citar valores e manda o link de planos.
6. Consulta o **Claude Code** (CLI) em modo texto, **sem nenhuma ferramenta** (`--tools ""`), com a base de
   conhecimento como prompt de sistema e a conversa pela entrada padrão. Ele entra com o login da assinatura
   (`CLAUDE_CODE_OAUTH_TOKEN`). A resposta é um JSON; se algo falhar (login, tempo, formato), o robô **transfere
   para o Almir Sousa** em vez de arriscar.
7. Limite de `MAX_RESPOSTAS_POR_HORA` (8) por conversa, contra loops. Uma consulta por vez (`CLAUDE_CONCORRENCIA=1`).

> **Aviso sobre o login da assinatura.** A página oficial de termos do Claude Code
> (https://code.claude.com/docs/en/legal-and-compliance) diz que quem constrói produtos ou serviços deve usar
> autenticação por chave de API, e que os limites dos planos Pro/Max assumem uso individual. Um robô que atende
> clientes é um serviço. O uso do login da assinatura aqui foi uma decisão do dono da conta, que assume o risco
> (por exemplo, bloqueio da conta). Para trocar o "cérebro" por outro (chave de API, 9router), basta reescrever
> `app/cerebro.py`: o resto do robô não muda.

## Publicar no Easypanel

1. **Token do Claude Code**: num computador com navegador, instale o Claude Code e rode `claude setup-token`;
   entre com a conta da assinatura. O token aparece no terminal (vale 1 ano). Não defina `ANTHROPIC_API_KEY`
   neste app: ela teria prioridade sobre o login da assinatura.
2. **Agente no Chatwoot**: crie um agente chamado "Atendente EnsinoPlus", entre com ele e copie o
   *Token de acesso* (Perfil). Adicione esse agente à caixa Waha (Caixas de entrada > waha > Agentes).
3. **App no Easypanel**: projeto `ensinoplus` > + Serviço > App
   - Fonte: GitHub `EnsinoPlus/atendente-claude`, branch `main`, build por **Dockerfile**
     (instala o Claude Code pelo instalador oficial).
   - Porta: `8000`.
   - Ambiente: copie `.env.example` e preencha (`CLAUDE_CODE_OAUTH_TOKEN`, `CHATWOOT_API_TOKEN`, `WEBHOOK_SEGREDO`).
     `CHATWOOT_URL=http://ensinoplus_chatwoot:3000` usa a rede interna do Easypanel.
   - Domínio: o Easypanel gera um `*.n697dr.easypanel.host`; pode usar esse.
   - Recursos: 1 processo só (o Dockerfile já usa `--workers 1`); cada consulta abre um processo do Claude Code.
4. Confira `https://<dominio>/saude` →
   `{"ok": true, "modo": "sugestao", "caixas": [11], "claude_instalado": true, "token_configurado": true}`.
5. **Webhook no Chatwoot**: Configurações > Integrações > Webhooks > Adicionar
   - URL: `https://<dominio>/webhooks/chatwoot/<WEBHOOK_SEGREDO>`
   - Evento: **Mensagem criada** (só esse).
6. Mande uma mensagem de teste pelo WhatsApp e veja a nota interna aparecer na conversa (~20 s).

## Ligar o modo automático (só depois de alguns dias de sugestões boas)

Troque `MODO=automatico` no Easypanel e reimplante. Para parar na hora: `MODO=desligado`.

## Testes

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -t .
```

Os testes não chamam o Claude Code nem o Chatwoot (usam dublês).

## Atualizar o que o robô sabe

Edite `conhecimento/atendimento.md` e reimplante. Preços **não** vão nesse arquivo: vêm da lista oficial.
Regra do arquivo: o robô só afirma o que está escrito ali ou na lista de preços; o resto vai para um humano.
