from __future__ import annotations

import pytest

from kline_hunter.config import AppConfig
from kline_hunter.locator import LocatorError, TdxLocator, compute_plan, is_windows


def test_plan_always_counts_back_from_latest():
    plan = compute_plan(2, 10)
    assert (plan.anchor_key, plan.direction, plan.steps) == ("end", "left", 7)
    plan = compute_plan(7, 10)
    assert (plan.anchor_key, plan.direction, plan.steps) == ("end", "left", 2)
    plan = compute_plan(0, 10)
    assert plan.steps == 9
    plan = compute_plan(9, 10)
    assert plan.steps == 0


def test_plan_rejects_bad_indices():
    with pytest.raises(LocatorError):
        compute_plan(-1, 10)
    with pytest.raises(LocatorError):
        compute_plan(10, 10)
    with pytest.raises(LocatorError):
        compute_plan(0, 0)


@pytest.mark.skipif(is_windows(), reason="只在非 Windows 环境测试")
def test_locator_rejects_non_windows(app_config: AppConfig):
    with pytest.raises(LocatorError, match="Windows"):
        TdxLocator(app_config).locate("000001", 10, 100)
