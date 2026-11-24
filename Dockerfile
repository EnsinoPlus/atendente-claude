FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# curl e bash: necessarios para o instalador oficial do Claude Code.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash atendente
USER atendente
ENV HOME=/home/atendente
ENV PATH="/home/atendente/.local/bin:${PATH}"

# Claude Code (instalador oficial, canal estavel).
RUN curl -fsSL https://claude.ai/install.sh | bash -s stable \
    && claude --version

# Depois de instalado: sem atualizacao automatica nem trafego nao essencial dentro do container.
ENV DISABLE_UPDATES=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CODE_SKIP_PROMPT_HISTORY=1

WORKDIR /srv
COPY --chown=atendente:atendente requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
ENV PATH="/home/atendente/.local/bin:${PATH}"

COPY --chown=atendente:atendente app ./app
COPY --chown=atendente:atendente conhecimento ./conhecimento

EXPOSE 8000
# Um unico processo: a espera entre mensagens e o limite por conversa ficam em memoria.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
