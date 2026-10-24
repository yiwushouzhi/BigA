from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .indicators import DERIVED_FIELDS, FIELD_ALIASES


class RuleValidationError(ValueError):
    """AI 返回的条件 JSON 不合法。"""


OPERATORS = {
    ">": ">",
    "gt": ">",
    ">=": ">=",
    "gte": ">=",
    "<": "<",
    "lt": "<",
    "<=": "<=",
    "lte": "<=",
    "==": "==",
    "=": "==",
    "eq": "==",
    "!=": "!=",
    "ne": "!=",
    "between": "between",
    "in": "in",
    "not_in": "not_in",
}

SCALAR_TYPES = (int, float, bool)


@dataclass(slots=True)
class Condition:
    field: str
    operator: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "operator": self.operator, "value": self.value}


@dataclass(slots=True)
class ScreenSpec:
    conditions: list[Condition] = field(default_factory=list)
    description: str = ""
    source: str = "ai"

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "conditions": [c.to_dict() for c in self.conditions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str = "ai") -> "ScreenSpec":
        if not isinstance(data, dict):
            raise RuleValidationError("条件必须是 JSON 对象")
        raw_conditions = data.get("conditions")
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise RuleValidationError("conditions 必须是非空数组")
        conditions = [_parse_condition(item) for item in raw_conditions]
        description = str(data.get("description") or "").strip()
        return cls(conditions=conditions, description=description, source=source)


def _normalize_field(name: Any) -> str:
    if not isinstance(name, str):
        raise RuleValidationError("field 必须是字符串")
    key = name.strip()
    key = FIELD_ALIASES.get(key, key)
    if key not in DERIVED_FIELDS:
        raise RuleValidationError(
            f"未知字段 {name!r}。可用字段见系统提示词中的字段列表"
        )
    return key


def _parse_condition(item: Any) -> Condition:
    if not isinstance(item, dict):
        raise RuleValidationError("conditions 中每个元素必须是对象")
    field_name = item.get("field", item.get("left"))
    field_name = _normalize_field(field_name)
    operator_raw = item.get("operator", item.get("op"))
    if not isinstance(operator_raw, str):
        raise RuleValidationError(f"{field_name} 缺少 operator")
    operator = OPERATORS.get(operator_raw.strip().lower().replace(" ", "_"))
    if operator is None:
        raise RuleValidationError(f"不支持的运算符: {operator_raw!r}")
    if "value" not in item:
        raise RuleValidationError(f"{field_name} 缺少 value")
    value = _validate_value(field_name, operator, item["value"])
    return Condition(field=field_name, operator=operator, value=value)


def _validate_value(field_name: str, operator: str, value: Any) -> Any:
    if operator in ("between", "in", "not_in"):
        if not isinstance(value, (list, tuple)) or not value:
            raise RuleValidationError(f"{field_name} 的 {operator} 值必须是数组")
        if operator == "between" and len(value) != 2:
            raise RuleValidationError(f"{field_name} 的 between 值必须恰好包含两个数")
        return [_validate_scalar(field_name, v) for v in value]

    if isinstance(value, str):
        _validate_expression(value)
        return value.strip()
    if isinstance(value, (list, tuple, dict)):
        raise RuleValidationError(f"{field_name} 的 value 不能是数组/对象")
    return _validate_scalar(field_name, value)


def _validate_scalar(field_name: str, value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise RuleValidationError(f"{field_name} 的数值必须是有限数")
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise RuleValidationError(f"{field_name} 的 value 不能为空")
        _validate_expression(stripped)
        return stripped
    raise RuleValidationError(f"{field_name} 的 value 类型不支持: {type(value).__name__}")


def _validate_expression(expr: str) -> None:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise RuleValidationError(f"表达式语法错误: {expr!r}") from exc

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.UAdd,
        ast.USub,
        ast.Call,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise RuleValidationError(f"表达式包含不允许的语法: {type(node).__name__}")
        if isinstance(node, ast.Name):
            name = node.id
            if name == "abs":
                continue
            if FIELD_ALIASES.get(name, name) not in DERIVED_FIELDS:
                raise RuleValidationError(f"表达式引用了未知字段: {name}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id != "abs":
                raise RuleValidationError("表达式只允许调用 abs()")
            if node.keywords or len(node.args) != 1:
                raise RuleValidationError("abs() 只能接受一个参数")


def _eval_expression(expr: str, df: pd.DataFrame) -> pd.Series | float:
    tree = ast.parse(expr, mode="eval")
    namespace = {name: df[name] for name in DERIVED_FIELDS if name in df.columns}
    for alias, target in FIELD_ALIASES.items():
        if target in df.columns:
            namespace[alias] = df[target]
    namespace["abs"] = np.abs
    return eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, namespace)


def _field_series(df: pd.DataFrame, name: str) -> pd.Series:
    name = FIELD_ALIASES.get(name, name)
    if name not in df.columns:
        raise RuleValidationError(f"数据缺少字段: {name}")
    return df[name]


def condition_mask(df: pd.DataFrame, condition: Condition) -> pd.Series:
    left = _field_series(df, condition.field)
    value = condition.value
    if isinstance(value, str):
        right: Any = _eval_expression(value, df)
    else:
        right = value

    operator = condition.operator
    if operator == "between":
        low, high = right
        if isinstance(low, str):
            low = _eval_expression(low, df)
        if isinstance(high, str):
            high = _eval_expression(high, df)
        mask = left.between(low, high, inclusive="both")
        valid = left.notna()
    elif operator in ("in", "not_in"):
        mask = left.isin(right)
        if operator == "not_in":
            mask = ~mask
        valid = left.notna()
    else:
        op = {
            ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
        }[operator]
        mask = op(left, right)
        mask = pd.Series(mask, index=df.index).fillna(False).astype(bool)
        valid = left.notna()
        if isinstance(right, pd.Series):
            valid = valid & right.notna()

    mask = pd.Series(mask, index=df.index).fillna(False).astype(bool) & valid
    return mask


def evaluate_mask(df: pd.DataFrame, spec: ScreenSpec) -> pd.Series:
    if df.empty or not spec.conditions:
        return pd.Series([], dtype=bool, index=df.index)
    mask = pd.Series(True, index=df.index)
    for condition in spec.conditions:
        mask &= condition_mask(df, condition)
    return mask


def humanize_spec(spec: ScreenSpec) -> str:
    op_text = {
        ">": "大于",
        ">=": "大于等于",
        "<": "小于",
        "<=": "小于等于",
        "==": "等于",
        "!=": "不等于",
        "between": "介于",
        "in": "属于",
        "not_in": "不属于",
    }
    parts = []
    for c in spec.conditions:
        parts.append(f"{c.field} {op_text.get(c.operator, c.operator)} {c.value}")
    return "；".join(parts)
