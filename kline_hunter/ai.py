from __future__ import annotations

import json
import re
from typing import Any

from .config import AppConfig
from .indicators import FIELD_HELP
from .rules import RuleValidationError, ScreenSpec


class AIError(RuntimeError):
    """DeepSeek 调用或返回内容异常。"""


SYSTEM_PROMPT = f"""
你是一个 A 股 K 线筛选条件生成器。用户用自然语言描述想找的 K 线形态，
你必须只输出一个 JSON 对象，软件会把 JSON 转成筛选逻辑，扫描全市场历史 K 线。
不要输出 Markdown，不要输出解释文字。

{FIELD_HELP}

运算符只允许：
  >, >=, <, <=, ==, !=, between, in, not_in
value 可以是数字、布尔值、数字数组，或者由上述字段组成的算术表达式字符串。
表达式只允许 + - * / // %、abs() 和字段名，例如 "vol_ma20 * 3"、"abs(body) * 2"。

输出 JSON 格式（严格）：
{{
  "description": "一句话说明筛选条件",
  "conditions": [
    {{"field": "is_bullish", "operator": "==", "value": true}},
    {{"field": "vol_ratio_20", "operator": ">", "value": 3}},
    {{"field": "lower_shadow_body_ratio", "operator": ">", "value": 2}}
  ]
}}

所有条件之间是 AND 关系。时间范围由软件界面单独控制，不要把“最近一年”之类写进条件。
如果用户的要求无法用上述字段表达，输出：
{{"error": "具体原因"}}
不要臆造字段名。

示例 1：
用户：找放巨量、长下影的阳线
输出：{{"description":"放量长下影阳线","conditions":[{{"field":"is_bullish","operator":"==","value":true}},{{"field":"vol_ratio_20","operator":">","value":3}},{{"field":"lower_shadow_body_ratio","operator":">","value":2}}]}}

示例 2：
用户：收盘价站上20日线，缩量回踩10日线附近且收阳
输出：{{"description":"20日线上方缩量回踩10日线收阳","conditions":[{{"field":"close","operator":">","value":"ma20"}},{{"field":"vol","operator":"<","value":"vol_ma20 * 0.8"}},{{"field":"low","operator":"<=","value":"ma10 * 1.01"}},{{"field":"is_bullish","operator":"==","value":true}}]}}

示例 3：
用户：涨幅超过5%，成交量比20日均量放大一倍以上
输出：{{"description":"放量上涨","conditions":[{{"field":"pct_chg","operator":">","value":5}},{{"field":"vol_ratio_20","operator":">","value":2}}]}}
""".strip()


class DeepSeekParser:
    def __init__(self, config: AppConfig, client: Any | None = None):
        self.config = config
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.config.deepseek_api_key:
            raise AIError(
                "未配置 DeepSeek API Key。请在“设置”中填写，或设置环境变量 DEEPSEEK_API_KEY。"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIError("缺少 openai 依赖，请执行 pip install openai") from exc
        self._client = OpenAI(
            api_key=self.config.deepseek_api_key,
            base_url=self.config.deepseek_base_url,
            timeout=60.0,
        )
        return self._client

    def parse(self, question: str) -> ScreenSpec:
        question = (question or "").strip()
        if not question:
            raise AIError("筛选条件不能为空")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        kwargs: dict[str, Any] = {
            "model": self.config.deepseek_model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            # 某些 OpenAI 兼容服务不认 response_format，去掉后再试一次。
            if "response_format" in str(exc).lower():
                kwargs.pop("response_format", None)
                try:
                    response = self.client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    raise AIError(f"DeepSeek 调用失败: {retry_exc}") from retry_exc
            else:
                raise AIError(f"DeepSeek 调用失败: {exc}") from exc

        content = getattr(response.choices[0].message, "content", None)
        if not content:
            raise AIError("DeepSeek 返回了空内容")
        payload = extract_json(content)
        if "error" in payload:
            raise AIError(f"DeepSeek 无法生成条件: {payload['error']}")
        try:
            spec = ScreenSpec.from_dict(payload, source="ai")
        except RuleValidationError as exc:
            raise AIError(f"DeepSeek 返回的条件不合法: {exc}") from exc
        return spec


def extract_json(text: str) -> dict[str, Any]:
    """兼容 ```json 代码块和前后夹杂说明文字的输出。"""
    cleaned = text.strip().lstrip("\ufeff")
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise AIError("DeepSeek 返回内容不是 JSON")
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIError(f"DeepSeek 返回 JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise AIError("DeepSeek 返回的 JSON 顶层必须是对象")
    return data
