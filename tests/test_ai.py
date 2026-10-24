from __future__ import annotations

from types import SimpleNamespace

import pytest

from kline_hunter.ai import AIError, DeepSeekParser, extract_json
from kline_hunter.config import AppConfig


class FakeCompletions:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def fake_client(content: str) -> tuple[SimpleNamespace, FakeCompletions]:
    completions = FakeCompletions(content)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_parse_ai_json(app_config: AppConfig):
    content = """
    ```json
    {"description":"放量长下影阳线","conditions":[
      {"field":"is_bullish","operator":"==","value":true},
      {"field":"vol_ratio_20","operator":">","value":3},
      {"field":"lower_shadow_body_ratio","operator":">","value":2}
    ]}
    ```
    """
    client, completions = fake_client(content)
    parser = DeepSeekParser(app_config, client=client)
    spec = parser.parse("找放巨量长下影阳线")

    assert spec.description == "放量长下影阳线"
    assert len(spec.conditions) == 3
    assert spec.conditions[1].field == "vol_ratio_20"
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert completions.calls[0]["model"] == app_config.deepseek_model


def test_extract_json_with_prefix_text():
    payload = extract_json('说明文字\n{"conditions":[{"field":"close","operator":">","value":1}]}\n结束')
    assert payload["conditions"][0]["field"] == "close"


def test_ai_error_payload(app_config: AppConfig):
    client, _ = fake_client('{"error":"无法表达"}')
    with pytest.raises(AIError, match="无法表达"):
        DeepSeekParser(app_config, client=client).parse("随便")


def test_ai_invalid_field(app_config: AppConfig):
    client, _ = fake_client(
        '{"conditions":[{"field":"magic","operator":">","value":1}]}'
    )
    with pytest.raises(AIError, match="不合法"):
        DeepSeekParser(app_config, client=client).parse("随便")


def test_missing_api_key(app_config: AppConfig):
    parser = DeepSeekParser(app_config)
    with pytest.raises(AIError, match="API Key"):
        parser.parse("放量阳线")


def test_empty_question(app_config: AppConfig):
    client, _ = fake_client("{}")
    with pytest.raises(AIError, match="不能为空"):
        DeepSeekParser(app_config, client=client).parse("   ")
