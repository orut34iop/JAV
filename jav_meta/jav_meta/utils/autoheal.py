import asyncio
import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from loguru import logger

from jav_meta.config import settings


class HealAction(str, Enum):
    REDUCE_CONCURRENCY = "reduce_concurrency"
    INCREASE_DELAY = "increase_delay"
    SWITCH_FALLBACK = "switch_fallback"
    PAUSE_AND_ALERT = "pause_and_alert"


@dataclass
class HealEvent:
    timestamp: datetime.datetime
    trigger: str
    action: HealAction
    old_value: str
    new_value: str
    message: str


@dataclass
class HealthSnapshot:
    window_pages: int = 10
    success_count: int = 0
    fail_count: int = 0
    empty_pages: int = 0
    avg_response_time: float = 0.0
    consecutive_failures: int = 0
    consecutive_empty: int = 0
    last_error_types: Dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.success_count + self.fail_count

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return self.success_count / self.total

    def record_success(self, response_time: float):
        self.success_count += 1
        self.consecutive_failures = 0
        self.avg_response_time = (self.avg_response_time * (self.total - 1) + response_time) / self.total

    def record_failure(self, error_type: str):
        self.fail_count += 1
        self.consecutive_failures += 1
        self.last_error_types[error_type] = self.last_error_types.get(error_type, 0) + 1

    def record_empty(self):
        self.empty_pages += 1
        self.consecutive_empty += 1

    def record_nonempty(self):
        self.consecutive_empty = 0


class AutoHealer:
    def __init__(
        self,
        on_reduce_concurrency: Optional[Callable[[int], None]] = None,
        on_increase_delay: Optional[Callable[[float], None]] = None,
        on_switch_fallback: Optional[Callable[[], None]] = None,
    ):
        self.snapshot = HealthSnapshot()
        self.events: List[HealEvent] = []
        self._pause_event = asyncio.Event()
        self._paused = False

        self.on_reduce_concurrency = on_reduce_concurrency
        self.on_increase_delay = on_increase_delay
        self.on_switch_fallback = on_switch_fallback

    def record_page_result(self, items_count: int, success: bool, error_type: str = "", response_time: float = 0.0):
        if success and items_count > 0:
            self.snapshot.record_success(response_time)
            self.snapshot.record_nonempty()
        elif success and items_count == 0:
            self.snapshot.record_success(response_time)
            self.snapshot.record_empty()
        else:
            self.snapshot.record_failure(error_type)

    async def evaluate(self) -> Optional[HealEvent]:
        """Evaluate health snapshot and trigger healing actions."""
        s = self.snapshot

        # Rule 1: Consecutive failures >= 5 -> reduce concurrency
        if s.consecutive_failures >= 5:
            return self._act(HealAction.REDUCE_CONCURRENCY, s,
                             trigger="consecutive_failures>=5")

        # Rule 2: Success rate < 30% over last window -> reduce concurrency + increase delay
        if s.total >= 5 and s.success_rate < 0.3:
            return self._act(HealAction.REDUCE_CONCURRENCY, s,
                             trigger=f"success_rate={s.success_rate:.1%}")

        # Rule 3: Consecutive empty pages >= 3 -> switch fallback parser
        if s.consecutive_empty >= 3:
            return self._act(HealAction.SWITCH_FALLBACK, s,
                             trigger="consecutive_empty>=3")

        # Rule 4: Too many connection errors -> increase delay
        conn_errors = s.last_error_types.get("connection", 0)
        if conn_errors >= 3:
            return self._act(HealAction.INCREASE_DELAY, s,
                             trigger=f"connection_errors={conn_errors}")

        return None

    def _act(self, action: HealAction, snapshot: HealthSnapshot, trigger: str) -> HealEvent:
        old_v = ""
        new_v = ""
        msg = ""

        if action == HealAction.REDUCE_CONCURRENCY:
            old_v = str(settings.concurrency)
            new_v = str(max(1, settings.concurrency // 2))
            msg = f"Reducing concurrency {old_v} -> {new_v} due to {trigger}"
            if self.on_reduce_concurrency:
                self.on_reduce_concurrency(int(new_v))

        elif action == HealAction.INCREASE_DELAY:
            old_v = f"{settings.request_delay}s"
            new_v = f"{settings.request_delay + 1.0}s"
            msg = f"Increasing delay {old_v} -> {new_v} due to {trigger}"
            if self.on_increase_delay:
                self.on_increase_delay(settings.request_delay + 1.0)

        elif action == HealAction.SWITCH_FALLBACK:
            old_v = "primary"
            new_v = "fallback"
            msg = f"Switching to fallback parser due to {trigger}"
            if self.on_switch_fallback:
                self.on_switch_fallback()

        elif action == HealAction.PAUSE_AND_ALERT:
            old_v = "running"
            new_v = "paused"
            msg = f"Pausing due to {trigger}"
            self._paused = True
            self._pause_event.set()

        event = HealEvent(
            timestamp=datetime.datetime.utcnow(),
            trigger=trigger,
            action=action,
            old_value=old_v,
            new_value=new_v,
            message=msg,
        )
        self.events.append(event)
        logger.warning(f"[AutoHeal] {msg}")
        return event

    def reset_window(self):
        self.snapshot = HealthSnapshot(window_pages=self.snapshot.window_pages)

    def is_paused(self) -> bool:
        return self._paused

    async def wait_if_paused(self):
        if self._paused:
            logger.info("[AutoHeal] Crawler paused, waiting for resume...")
            await self._pause_event.wait()

    def resume(self):
        self._paused = False
        self._pause_event.clear()
        logger.info("[AutoHeal] Crawler resumed.")
