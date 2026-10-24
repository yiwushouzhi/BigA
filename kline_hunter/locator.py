from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass
from typing import Callable

from .config import AppConfig

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]

VK_LEFT = 0x25
VK_RIGHT = 0x27
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


class LocatorError(RuntimeError):
    pass


@dataclass(slots=True)
class LocatePlan:
    anchor_key: str
    steps: int
    direction: str
    bar_index: int
    total_bars: int


def compute_plan(bar_index: int, total_bars: int) -> LocatePlan:
    """统一从最新一根（End）向左移动。

    只从尾部对齐，是因为本地最多保存 8000 根，而通达信可能显示更早的历史；
    但“目标K线到今天”这一段两边是一致的，所以从 End 向左数不会受截断影响。
    """
    if total_bars <= 0:
        raise LocatorError("K线总根数必须大于0")
    if bar_index < 0 or bar_index >= total_bars:
        raise LocatorError(f"bar_index 超出范围: {bar_index}/{total_bars}")
    return LocatePlan("end", total_bars - 1 - bar_index, "left", bar_index, total_bars)


def is_windows() -> bool:
    return sys.platform == "win32"


class TdxLocator:
    """Windows 上激活通达信，输入代码、切 K 线并移动十字光标。"""

    def __init__(self, config: AppConfig):
        self.config = config
        self._hwnd: int | None = None
        self._pyautogui = None

    def locate(
        self,
        code: str,
        bar_index: int,
        total_bars: int,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> LocatePlan:
        if not is_windows():
            raise LocatorError("通达信联动仅支持 Windows。当前系统只能筛选和查看数据。")
        plan = compute_plan(bar_index, total_bars)
        hwnd = self._find_window()
        self._activate(hwnd)

        pyautogui = self._get_pyautogui()
        if progress:
            progress(0, plan.steps, "激活通达信")
        self._activate(hwnd)
        pyautogui.write(str(code), interval=0.03)
        pyautogui.press("enter")
        time.sleep(0.8)
        pyautogui.press("f5")
        time.sleep(1.0)

        if cancelled and cancelled():
            raise LocatorError("定位已取消")
        pyautogui.press(plan.anchor_key)
        time.sleep(0.15)

        key = "right" if plan.direction == "right" else "left"
        for sent in range(1, plan.steps + 1):
            if cancelled and cancelled():
                raise LocatorError("定位已取消")
            self._press_arrow(key)
            if sent % 100 == 0:
                self._ensure_foreground(hwnd)
                if progress:
                    progress(sent, plan.steps, f"移动十字光标 {sent}/{plan.steps}")
        if progress:
            progress(plan.steps, plan.steps, "定位完成")
        return plan

    # ---------------------------------------------------------------- windows
    def _find_window(self) -> int:
        try:
            import win32gui
        except ImportError as exc:
            raise LocatorError("缺少 pywin32 依赖，无法控制通达信窗口") from exc

        keyword = (self.config.tdx_window_keyword or "通达信").lower()
        candidates: list[tuple[int, int, str]] = []

        def callback(hwnd: int, _extra: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if keyword not in title.lower():
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            area = max(0, right - left) * max(0, bottom - top)
            candidates.append((area, hwnd, title))

        win32gui.EnumWindows(callback, None)
        if not candidates:
            raise LocatorError(
                f"没有找到标题包含“{self.config.tdx_window_keyword}”的窗口，请先打开通达信"
            )
        candidates.sort(reverse=True)
        self._hwnd = candidates[0][1]
        return self._hwnd

    def _activate(self, hwnd: int) -> None:
        try:
            import win32con
            import win32gui
        except ImportError as exc:
            raise LocatorError("缺少 pywin32 依赖，无法控制通达信窗口") from exc
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
        time.sleep(0.3)
        if win32gui.GetForegroundWindow() != hwnd:
            raise LocatorError("无法把通达信切到最前端，请先手动点击通达信窗口后再试")

    def _ensure_foreground(self, hwnd: int) -> None:
        try:
            import win32gui
        except ImportError:
            return
        if win32gui.GetForegroundWindow() != hwnd:
            self._activate(hwnd)

    def _press_arrow(self, key: str) -> None:
        """用 keybd_event 发送方向键，比 pyautogui 每次按键更快。"""
        if not is_windows():
            raise LocatorError("通达信联动仅支持 Windows")
        vk = VK_RIGHT if key == "right" else VK_LEFT
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        # 交给通达信消息循环处理；过长会导致定位非常慢，过短可能丢键。
        time.sleep(max(0.002, self.config.tdx_key_interval))

    def _get_pyautogui(self):
        if self._pyautogui is not None:
            return self._pyautogui
        try:
            import pyautogui
        except ImportError as exc:
            raise LocatorError("缺少 pyautogui 依赖，无法模拟通达信键盘输入") from exc
        pyautogui.PAUSE = 0.01
        pyautogui.FAILSAFE = False
        self._pyautogui = pyautogui
        return pyautogui
