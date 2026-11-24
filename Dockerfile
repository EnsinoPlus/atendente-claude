FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY conhecimento ./conhecimento

RUN useradd --create-home atendente
USER atendente

EXPOSE 8000
# Um unico processo: a espera entre mensagens e o limite por conversa ficam em memoria.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
