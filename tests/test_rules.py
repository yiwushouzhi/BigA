from __future__ import annotations

import pandas as pd
import pytest

from kline_hunter.indicators import add_indicators
from kline_hunter.rules import (
    RuleValidationError,
    ScreenSpec,
    evaluate_mask,
    humanize_spec,
)


def test_long_lower_shadow_with_volume(pattern_frame: pd.DataFrame):
    enriched = add_indicators(pattern_frame)
    spec = ScreenSpec.from_dict(
        {
            "description": "放量长下影阳线",
            "conditions": [
                {"field": "is_bullish", "operator": "==", "value": True},
                {"field": "vol_ratio_20", "operator": ">", "value": 2.5},
                {"field": "lower_shadow_body_ratio", "operator": ">", "value": 2},
            ],
        }
    )
    mask = evaluate_mask(enriched, spec)
    assert mask.tolist().count(True) == 1
    assert bool(mask.iloc[-1]) is True
    assert enriched.loc[mask, "date"].iloc[0] == pd.Timestamp("2024-02-02")


def test_expression_and_between(pattern_frame: pd.DataFrame):
    enriched = add_indicators(pattern_frame)
    spec = ScreenSpec.from_dict(
        {
            "conditions": [
                {"field": "close", "operator": ">", "value": "ma20"},
                {"field": "close", "operator": "between", "value": [10.5, 11.5]},
                {"field": "vol", "operator": ">", "value": "vol_ma20 * 2"},
            ]
        }
    )
    mask = evaluate_mask(enriched, spec)
    assert bool(mask.iloc[-1]) is True
    assert mask.sum() == 1


def test_aliases_are_normalized():
    spec = ScreenSpec.from_dict(
        {
            "conditions": [
                {"field": "volume", "operator": "gt", "value": "ma20_vol * 2"},
                {"field": "is_yang", "operator": "eq", "value": True},
            ]
        }
    )
    assert spec.conditions[0].field == "vol"
    assert spec.conditions[0].operator == ">"
    assert spec.conditions[0].value == "ma20_vol * 2"
    assert spec.conditions[1].field == "is_bullish"
    assert "vol" in humanize_spec(spec)


def test_invalid_fields_and_code_rejected():
    with pytest.raises(RuleValidationError):
        ScreenSpec.from_dict(
            {"conditions": [{"field": "does_not_exist", "operator": ">", "value": 1}]}
        )
    with pytest.raises(RuleValidationError):
        ScreenSpec.from_dict(
            {
                "conditions": [
                    {
                        "field": "close",
                        "operator": ">",
                        "value": "__import__('os').system('echo hacked')",
                    }
                ]
            }
        )
    with pytest.raises(RuleValidationError):
        ScreenSpec.from_dict({"conditions": []})


def test_nan_does_not_match(pattern_frame: pd.DataFrame):
    enriched = add_indicators(pattern_frame.head(5))
    spec = ScreenSpec.from_dict(
        {"conditions": [{"field": "ma20", "operator": ">", "value": 1}]}
    )
    mask = evaluate_mask(enriched, spec)
    assert not mask.any()
