"""Thin wrapper around any OpenAI-compatible chat-completions endpoint with tool calling."""

import math
import os
import time
import uuid

from report import register_secret


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, model=None, api_key=None, base_url=None, timeout=None):
        from openai import OpenAI

        api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMError("No API key configured. Set LLM_API_KEY (see .env.example).")
        register_secret(api_key)
        self.model = model or os.getenv("LLM_MODEL") or "gpt-5.4-mini"
        temperature = os.getenv("LLM_TEMPERATURE", "").strip()
        try:
            timeout = float(timeout if timeout is not None else os.getenv('LLM_TIMEOUT', '90'))
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError('timeout must be positive')
            self.extra = {"temperature": float(temperature)} if temperature else {}
            if temperature and (not math.isfinite(self.extra['temperature']) or not 0 <= self.extra['temperature'] <= 2):
                raise ValueError('temperature must be between 0 and 2')
        except ValueError as e:
            raise LLMError(f'Invalid LLM configuration: {e}') from e
        effort = os.getenv('LLM_REASONING_EFFORT', '').strip()
        if effort:
            if effort not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}:
                raise LLMError('Invalid LLM_REASONING_EFFORT; see .env.example.')
            self.extra['reasoning_effort'] = effort
        fallbacks = [m.strip() for m in os.getenv('LLM_FALLBACK_MODELS', '').split(',') if m.strip()]
        if fallbacks:
            # OpenRouter routes to the next model when one is rate-limited or unavailable.
            self.extra['extra_body'] = {'models': [self.model, *fallbacks]}
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or os.getenv("LLM_BASE_URL") or None,
            timeout=timeout,
            max_retries=2,  # SDK retries rate limits, timeouts and 5xx with backoff
        )

    def chat(self, messages, tools):
        """Send the conversation; return the assistant message as a plain dict."""
        import openai

        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=messages, tools=tools, **self.extra
                )
            except openai.APIError as e:
                raise LLMError(f"LLM request failed: {e}") from e
            if resp.choices:
                break
            # Some gateways (e.g. OpenRouter) report upstream failures as a 200 with an error body.
            time.sleep(2 * (attempt + 1))
        else:
            detail = (resp.model_extra or {}).get('error') or 'no details'
            raise LLMError(f"LLM returned no choices after 3 attempts: {detail}")
        if resp.choices[0].finish_reason == 'length':
            raise LLMError('LLM response was truncated; use a model with a larger output limit.')

        msg = resp.choices[0].message
        if msg.refusal:
            raise LLMError('LLM declined the audit request.')
        out = {"role": "assistant", "content": msg.content or ""}
        # Some OpenAI-compatible providers need reasoning text replayed with tool calls.
        if getattr(msg, 'reasoning_content', None):
            out['reasoning_content'] = msg.reasoning_content
        if msg.tool_calls:
            out['tool_calls'] = []
            for tc in msg.tool_calls:
                # Preserve provider extensions such as Gemini's opaque thought signatures.
                item = tc.model_dump(exclude_none=True)
                item['id'] = tc.id or f'call_{uuid.uuid4().hex[:12]}'
                item['function']['arguments'] = tc.function.arguments or '{}'
                out['tool_calls'].append(item)
        return out
