"""Thin wrapper around any OpenAI-compatible chat-completions endpoint with tool calling."""

import os
import uuid


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, model=None, api_key=None, base_url=None, timeout=180):
        from openai import OpenAI

        api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMError("No API key configured. Set LLM_API_KEY (see .env.example).")
        self.model = model or os.getenv("LLM_MODEL") or "gpt-4.1-mini"
        temperature = os.getenv("LLM_TEMPERATURE", "0").strip()
        self.extra = {"temperature": float(temperature)} if temperature else {}
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or os.getenv("LLM_BASE_URL") or None,
            timeout=timeout,
            max_retries=5,  # SDK retries rate limits, timeouts and 5xx with backoff
        )

    def chat(self, messages, tools):
        """Send the conversation; return the assistant message as a plain dict."""
        import openai

        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools, **self.extra
            )
        except openai.APIError as e:
            raise LLMError(f"LLM request failed: {e}") from e
        if not resp.choices:
            raise LLMError("LLM returned no choices")

        msg = resp.choices[0].message
        out = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id or f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                }
                for tc in msg.tool_calls
            ]
        return out
