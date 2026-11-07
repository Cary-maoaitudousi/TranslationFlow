"""把审核请求从后台线程送回 GUI 线程（阻塞等待结果）。

Qt 的对话框只能在 GUI 线程创建，而流水线跑在 QThread 里。这里用一个
"事件 + 可变的 holder + threading.Event" 把请求转发给主窗口，后台线程阻塞等待，
用户在窗口里点完再继续。超时视为取消，避免主窗口关闭后永久卡住。
"""

from __future__ import annotations

import threading
from typing import Callable

from ..logger import get_logger
from ..review import ReviewCancelled

log = get_logger("review")

EventFn = Callable[[str, dict], None]


class GuiThreadReviewer:
    def __init__(self, emit: EventFn, timeout_s: int = 1800):
        self.emit = emit
        self.timeout_s = timeout_s

    def _request(self, kind: str, payload: dict):
        holder: dict = {}
        done = threading.Event()
        payload = dict(payload)
        payload["holder"] = holder
        payload["done"] = done
        self.emit(kind, payload)
        if not done.wait(self.timeout_s):
            log.error(f"审核请求超时（{self.timeout_s}s）：{kind}")
            raise ReviewCancelled("审核超时")
        if holder.get("cancelled"):
            raise ReviewCancelled("用户在审核窗口取消")
        if "value" not in holder:
            # 界面层出错时兜底：宁可当作取消，也不要把 None 丢给流水线
            log.error(f"审核结果缺失（{kind}），按取消处理")
            raise ReviewCancelled(f"审核结果缺失：{kind}")
        return holder["value"]

    def review_nouns(self, entries, global_table):
        return self._request("review_nouns", {"entries": entries, "table": global_table})

    def review_polish(self, entries, translated_text):
        return self._request("review_polish", {"entries": entries, "text": translated_text})

    def choose_title(self, titles):
        return self._request("review_title", {"entries": titles})
