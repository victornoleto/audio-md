"""Summarize a transcript into Markdown via a headless agent CLI.

No API key is managed here — each provider CLI authenticates through its own
login (see ``providers.py``). The provider/model is chosen in ``config.py``.
"""

from __future__ import annotations

from audio_md.providers import call_llm

PROMPT = """Você é um assistente que resume transcrições de áudio em português.

Gere um resumo em **Markdown** com esta estrutura:

## TL;DR
Um parágrafo curto (2-3 frases) com a essência do conteúdo.

## Tópicos principais
- bullets com os pontos discutidos

## Decisões e ações
- decisões tomadas e próximos passos/tarefas (se houver; senão, escreva "Nenhuma identificada").

Seja fiel ao conteúdo, não invente informação. Responda apenas com o Markdown do resumo.

A transcrição do áudio segue abaixo."""


def summarize(transcript: str, model: str, provider: str) -> str:
    """Run the configured provider CLI on the transcript. Returns the markdown."""
    md = call_llm(PROMPT, transcript, model, provider).strip()
    if not md:
        raise RuntimeError(f"{provider} returned an empty result")
    return md
