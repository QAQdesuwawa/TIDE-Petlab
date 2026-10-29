"""Concrete one-shot wall-clock driver for persisted product plans.

The driver owns waiting only. It never decides whether an observation is due,
advances a TemporalDomain, commits a tick, or emits an action. At the derived
schedule it merely asks the owner-bound host to sample once; the owner Actor and
Runner retain every state-changing authority.
"""
from __future__ import annotations

import threading
from collections.abc import Callable

from framework.temporal_domain import TemporalDomainContractError
from temporal_packages.wall_clock_ms_v1 import WallClockMillisecondsV1
from product_adapters.wall_clock_ms_v1.product_host import (
    WallClockProductObservationHost,
)


class WallClockProductObservationDriver:
    """Wait for the current active plan and submit one owner-bound sample."""

    def __init__(
        self,
        *,
        host: WallClockProductObservationHost,
        coordinate_reader: Callable[[], object],
    ) -> None:
        if not isinstance(host, WallClockProductObservationHost):
            raise TypeError("wall-clock driver requires a product host")
        if not callable(coordinate_reader):
            raise TypeError("wall-clock driver coordinate_reader must be callable")
        self._host = host
        self._coordinate_reader = coordinate_reader
        self._changed = threading.Event()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._start_lock = threading.Lock()

    def start(self):
        with self._start_lock:
            if self._stopped.is_set():
                raise RuntimeError("wall-clock driver is already stopped")
            if not self._thread.is_alive():
                self._thread.start()
        self.notify_active_plan_changed()
        return self

    def notify_active_plan_changed(self) -> None:
        """Wake the waiter; the notification carries no plan or time data."""
        if not self._stopped.is_set():
            self._changed.set()

    def stop(self) -> None:
        self._stopped.set()
        self._changed.set()
        if (
            threading.current_thread() is not self._thread
            and self._thread.is_alive()
        ):
            self._thread.join(timeout=2.0)

    def _read_coordinate(self) -> int:
        return WallClockMillisecondsV1._milliseconds(
            self._coordinate_reader(), "coordinate")

    def _run(self) -> None:
        while not self._stopped.is_set():
            self._changed.wait()
            self._changed.clear()
            if self._stopped.is_set():
                return
            while not self._stopped.is_set():
                try:
                    schedule = self._host.next_wake_schedule()
                except (ValueError, TemporalDomainContractError):
                    # No valid active pointer exists yet. Only a later Actor
                    # completion notification may make a schedule available.
                    break
                now_ms = self._read_coordinate()
                full_wait_seconds = max(
                    0.0, (schedule.at_ms - now_ms) / 1000.0)
                wait_seconds = min(
                    full_wait_seconds,
                    max(0.0, float(threading.TIMEOUT_MAX) - 1.0),
                )
                if self._changed.wait(wait_seconds):
                    self._changed.clear()
                    continue
                if self._stopped.is_set():
                    return
                if wait_seconds < full_wait_seconds:
                    # Runtime wait APIs have a finite timeout range. A bounded
                    # sleep is not a due proof: re-read the coordinate and the
                    # same durable plan instead of sampling early.
                    continue
                try:
                    self._host.sample_scheduled(schedule)
                except ValueError:
                    # The plan was replaced between waiting and sampling.
                    # Re-derive from the durable active pointer; never sample
                    # or consume the replacement through the stale schedule.
                    continue
                # The typed wake is now queued. Wait for the owner Actor's
                # committed completion notification before deriving again.
                break


__all__ = ("WallClockProductObservationDriver",)
