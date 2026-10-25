import json

import openai
import pytest

from llm import LLM, LLMError

try:
    import httpx2 as httpx
except ImportError:  # OpenAI SDK 1.x/2.x
    import httpx


@pytest.fixture
def api(monkeypatch):
    """Exercise the real SDK's wire format without network requests or credentials."""
    for name in ['LLM_API_KEY', 'OPENAI_API_KEY', 'LLM_MODEL', 'LLM_BASE_URL', 'LLM_TEMPERATURE',
                 'LLM_REASONING_EFFORT', 'LLM_TIMEOUT']:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('LLM_API_KEY', 'offline-test-placeholder')
    requests = []
    response = {'id': 'test', 'object': 'chat.completion', 'created': 0, 'model': 'test', 'choices': [
        {'index': 0, 'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': 'Inspect files.',
         'tool_calls': [{'id': 'c1', 'type': 'function',
                         'function': {'name': 'list_files', 'arguments': '{}'}}]}}]}

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    client_class = openai.OpenAI
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: client_class(
        **kw, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    return requests, response


def test_tool_calls_and_reasoning_settings(api, monkeypatch):
    requests, _ = api
    monkeypatch.setenv('LLM_REASONING_EFFORT', 'medium')
    client = LLM()
    reply = client.chat([{'role': 'user', 'content': 'Audit'}], [])
    assert requests[0]['model'] == 'gpt-5.4-mini'
    assert requests[0]['reasoning_effort'] == 'medium'
    assert 'temperature' not in requests[0]
    assert reply['tool_calls'][0]['function']['name'] == 'list_files'


@pytest.mark.parametrize('name,value', [('LLM_TIMEOUT', 'nope'), ('LLM_TIMEOUT', '-1'),
                                     ('LLM_TEMPERATURE', 'nan'), ('LLM_REASONING_EFFORT', 'unlimited')])
def test_bad_config_is_a_useful_error(api, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(LLMError, match='Invalid LLM'):
        LLM()


def test_rejects_truncated_reply(api):
    _, response = api
    response['choices'][0]['finish_reason'] = 'length'
    with pytest.raises(LLMError, match='truncated'):
        LLM().chat([{'role': 'user', 'content': 'Audit'}], [])


def test_provider_tool_metadata_survives_followup(api):
    requests, response = api
    response['choices'][0]['message']['tool_calls'][0]['extra_content'] = {
        'google': {'thought_signature': 'opaque-provider-metadata'}}
    client = LLM()
    messages = [{'role': 'user', 'content': 'Audit'}]
    messages.append(client.chat(messages, []))
    messages.append({'role': 'tool', 'tool_call_id': 'c1', 'content': 'files'})
    client.chat(messages, [])
    assert requests[1]['messages'][1]['tool_calls'][0]['extra_content']['google']['thought_signature'] == (
        'opaque-provider-metadata')


def test_wraps_provider_failure(api, monkeypatch):
    client = LLM()

    def fail(**kwargs):
        raise openai.APIConnectionError(request=httpx.Request('POST', 'https://example.invalid'))

    monkeypatch.setattr(client.client.chat.completions, 'create', fail)
    with pytest.raises(LLMError, match='LLM request failed'):
        client.chat([], [])
