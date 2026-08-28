# -*- coding: utf-8 -*-
"""Клиент для OpenAI-совместимых LLM-провайдеров (vLLM, Yandex GPT, Azure OpenAI и др.).

Использует чистый urllib (не SDK), чтобы chat_template_kwargs попадал в корень
тела запроса — обязательно для vLLM-провайдеров при отключении рассуждений.
"""
import json
import re
import urllib.request
import urllib.error
from typing import Optional


def call_ai_provider(
    base_url: str,
    api_key: str,
    model: str,
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    enable_thinking: Optional[bool] = False,
    timeout: int = 60,
    response_format: Optional[dict] = None,
) -> dict:
    """POST /chat/completions к OpenAI-совместимому провайдеру.

    Returns:
        {
            'content': str,
            'usage': {'prompt_tokens': int, 'completion_tokens': int}
        }
    Raises:
        Exception  (с атрибутом .status при HTTP-ошибке, без — при сетевых проблемах)
    """
    url = base_url.rstrip('/') + '/chat/completions'

    body: dict = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }

    # ⚠️  Режим без рассуждений — ТОЛЬКО через корень тела.
    # OpenAI SDK обворачивает extra_body — они не доходят до vLLM.
    if enable_thinking is False:
        body['chat_template_kwargs'] = {'enable_thinking': False}
    if response_format:
        body['response_format'] = response_format

    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode('utf-8', errors='replace')[:300]
        err = Exception(f'AI HTTP {e.code}: {body_text}')
        err.status = e.code  # type: ignore[attr-defined]
        raise err
    except urllib.error.URLError as e:
        raise Exception(f'Ошибка подключения к AI-провайдеру: {e.reason}')
    except TimeoutError:
        raise Exception('Таймаут ожидания ответа от AI-провайдера')

    message = (result.get('choices') or [{}])[0].get('message') or {}
    content: str = message.get('content') or ''

    # Стрипаем <think>...</think> — некоторые vLLM-провайдеры возвращают их
    # даже при enable_thinking=False
    content = re.sub(r'<think>[\s\S]*?</think>', '', content, flags=re.IGNORECASE).strip()

    usage = result.get('usage') or {}
    return {
        'content': content,
        'usage': {
            'prompt_tokens':     usage.get('prompt_tokens', 0),
            'completion_tokens': usage.get('completion_tokens', 0),
        },
    }
