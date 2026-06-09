"""Data collector with numpy ring buffer and background sampling thread."""

import threading
import sys
import time
from typing import Optional

import numpy as np

from src.core.models import TypeInfo
from src.core.transports import VariableReadTransport


class RingBuffer:
    """Pre-allocated numpy ring buffer with O(1) append, O(log n) search, O(k) tail."""

    __slots__ = ('_data', '_head', '_count', '_maxlen')

    def __init__(self, maxlen: int):
        self._data = np.empty(maxlen, dtype=np.float64)
        self._head = 0       # next write position
        self._count = 0
        self._maxlen = maxlen

    def append(self, val: float):
        self._data[self._head] = val
        self._head += 1
        if self._head == self._maxlen:
            self._head = 0
        if self._count < self._maxlen:
            self._count += 1

    @property
    def count(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    def _phys_idx(self, logical_idx: int) -> int:
        if logical_idx < 0:
            logical_idx = self._count + logical_idx
        return (self._head - self._count + logical_idx) % self._maxlen

    def logical_slice(self, start: int, stop: int) -> np.ndarray:
        """Return data[start:stop] as contiguous numpy array (view if possible)."""
        if start < 0:
            start = max(0, self._count + start)
        if stop < 0:
            stop = max(0, self._count + stop)
        start = max(0, min(start, self._count))
        stop = max(0, min(stop, self._count))
        if start >= stop:
            return np.array([], dtype=np.float64)

        p_start = (self._head - self._count + start) % self._maxlen
        p_end = (self._head - self._count + stop) % self._maxlen

        if p_end == 0 and stop - start == self._count:
            p_end = self._maxlen

        if p_start < p_end:
            return self._data[p_start:p_end]  # view, no copy
        else:
            # Wrapped: must copy to make contiguous
            return np.concatenate([self._data[p_start:], self._data[:p_end]])

    def find_ge(self, value: float) -> int:
        """Return logical index of first element >= value.

        Uses binary search directly on physical memory views (no copy).
        """
        if self._count == 0:
            return -1

        p_start = (self._head - self._count) % self._maxlen
        p_end = self._head

        if p_start < p_end:
            # Single contiguous segment — searchsorted on view (O(log n), no copy)
            idx = np.searchsorted(self._data[p_start:p_end], value)
            return idx if idx < self._count else -1
        else:
            # Wrapped: two segments. Search first, then second if needed.
            seg1_len = self._maxlen - p_start
            idx = np.searchsorted(self._data[p_start:], value)
            if idx < seg1_len:
                return idx
            idx2 = np.searchsorted(self._data[:p_end], value)
            if idx2 < p_end:
                return seg1_len + idx2
            return -1

    def all_data(self) -> np.ndarray:
        """Return all data as contiguous array (copy only if wrapped)."""
        if self._count == 0:
            return np.array([], dtype=np.float64)
        p_start = (self._head - self._count) % self._maxlen
        p_end = self._head
        if p_end == 0 and self._count == self._maxlen:
            p_end = self._maxlen
        if p_start < p_end:
            return self._data[p_start:p_end]  # view
        else:
            return np.concatenate([self._data[p_start:], self._data[:p_end]])


class DataCollector:
    def __init__(self):
        self._backend: Optional[VariableReadTransport] = None
        self._variables: list[tuple[str, int, TypeInfo]] = []
        self._buffers: dict[str, RingBuffer] = {}
        self._timestamps: Optional[RingBuffer] = None
        self._sample_rate = 100
        self._buffer_size = 1000
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._actual_rate = 0.0
        self._sample_count = 0
        self._t0 = 0.0
        self._timer_precision_active = False

    def set_backend(self, backend: VariableReadTransport):
        self._backend = backend

    def configure(self, sample_rate: int, buffer_seconds: float = 10.0):
        self._sample_rate = max(1, int(sample_rate))
        self._buffer_size = max(int(self._sample_rate * buffer_seconds), 1000)

    def set_sample_rate(self, sample_rate: int):
        with self._lock:
            self._sample_rate = max(1, int(sample_rate))
        self._wake_event.set()

    def set_variables(self, variables: list[tuple[str, int, TypeInfo]]):
        with self._lock:
            self._variables = variables
            self._buffers = {v[0]: RingBuffer(self._buffer_size) for v in variables}
            self._timestamps = RingBuffer(self._buffer_size)

    def add_variable(self, name: str, address: int, type_info: TypeInfo):
        with self._lock:
            self._variables.append((name, address, type_info))
            self._buffers[name] = RingBuffer(self._buffer_size)

    def remove_variable(self, name: str):
        with self._lock:
            self._variables = [(n, a, t) for n, a, t in self._variables if n != name]
            if name in self._buffers:
                del self._buffers[name]

    @property
    def variable_names(self) -> list[str]:
        return [v[0] for v in self._variables]

    def start(self):
        if self._running:
            return
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)
            if self._thread.is_alive():
                raise RuntimeError("Previous sampling thread is still stopping")
        if not self._backend or not self._backend.is_connected:
            raise RuntimeError("Backend not connected")
        self._begin_timer_precision()
        self._running = True
        self._wake_event.clear()
        self._sample_count = 0
        self._t0 = 0.0
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def request_stop(self):
        self._running = False
        self._wake_event.set()

    def stop(self, timeout: float = 1.0) -> bool:
        self.request_stop()
        stopped = True
        if self._thread:
            self._thread.join(timeout=max(0.0, float(timeout)))
            stopped = not self._thread.is_alive()
            if stopped:
                self._thread = None
        self._end_timer_precision()
        return stopped

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def actual_rate(self) -> float:
        return self._actual_rate

    def get_data(self, tail_seconds: float = None) -> dict[str, tuple['np.ndarray', 'np.ndarray']]:
        """Returns {name: (timestamps, values)} as numpy arrays.

        If tail_seconds is provided, returns only data from the last N seconds.
        """
        with self._lock:
            if self._timestamps is None or self._timestamps.count == 0:
                return {}

            ts_buf = self._timestamps

            if tail_seconds is not None and tail_seconds > 0:
                last_val = ts_buf._data[ts_buf._phys_idx(-1)]
                cutoff = last_val - tail_seconds
                start_idx = ts_buf.find_ge(cutoff)
                if start_idx < 0:
                    start_idx = 0
            else:
                start_idx = 0

            ts_arr = ts_buf.logical_slice(start_idx, ts_buf.count).copy()

            result = {}
            for name, buf in self._buffers.items():
                vals_arr = buf.logical_slice(start_idx, buf.count).copy()
                n = min(len(ts_arr), len(vals_arr))
                result[name] = (ts_arr[:n], vals_arr[:n])
            return result

    def _sample_loop(self):
        t0 = time.perf_counter()
        self._t0 = t0
        rate_window = 10

        while self._running:
            loop_start = time.perf_counter()
            batch_size = 1
            produced_count = 1

            try:
                with self._lock:
                    variables_snapshot = list(self._variables)
                    buffers_snapshot = [self._buffers.get(name) for name, _, _ in variables_snapshot]
                    sample_rate = max(1, self._sample_rate)
                    timestamps = self._timestamps
                sample_interval = 1.0 / sample_rate
                if variables_snapshot:
                    batch_size = _batch_size_for_rate(sample_rate)
                    batch_start = time.perf_counter()
                    if hasattr(self._backend, "read_batch_rows"):
                        samples = self._backend.read_batch_rows(variables_snapshot, batch_size)
                    elif batch_size > 1 and hasattr(self._backend, "read_batch_samples"):
                        samples = self._backend.read_batch_samples(variables_snapshot, batch_size)
                    else:
                        samples = [self._backend.read_batch(variables_snapshot)]
                    if not self._running:
                        break
                    produced_count = max(1, len(samples))
                    if timestamps is not None:
                        with self._lock:
                            for index, values in enumerate(samples):
                                now = batch_start - t0 + index * sample_interval
                                timestamps.append(now)
                                if isinstance(values, dict):
                                    for pos, (name, _, _) in enumerate(variables_snapshot):
                                        buf = buffers_snapshot[pos]
                                        if buf is not None:
                                            buf.append(values.get(name, float("nan")))
                                else:
                                    for buf, val in zip(buffers_snapshot, values):
                                        if buf is not None:
                                            buf.append(val)

                    self._sample_count += len(samples)
                    if self._sample_count % rate_window == 0:
                        elapsed = time.perf_counter() - t0
                        self._actual_rate = self._sample_count / elapsed if elapsed > 0 else 0

            except Exception as e:
                if not self._running:
                    break
                import sys
                print(f"[Collector] sample error: {e}", file=sys.stderr, flush=True)

            # Maintain sample rate
            elapsed = time.perf_counter() - loop_start
            sleep_time = sample_interval * produced_count - elapsed
            if sleep_time > 0:
                self._wake_event.wait(sleep_time)
                self._wake_event.clear()

    def _begin_timer_precision(self):
        if self._timer_precision_active:
            return
        if sys.platform != "win32":
            return
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
            self._timer_precision_active = True
        except Exception:
            pass

    def _end_timer_precision(self):
        if not self._timer_precision_active:
            return
        if sys.platform != "win32":
            self._timer_precision_active = False
            return
        try:
            import ctypes
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass
        self._timer_precision_active = False


def _batch_size_for_rate(sample_rate: int) -> int:
    if sample_rate >= 1000:
        return 24
    if sample_rate >= 500:
        return 16
    if sample_rate >= 200:
        return 8
    return 1
