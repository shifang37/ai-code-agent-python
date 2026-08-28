"""雪花 ID 生成器 —— 与 Java 版 MyBatis-Flex 的 snowFlakeId 主键策略保持同构。

新旧两套服务写同一张表，ID 必须落在同一数量级且不冲突。这里用独立的
worker/datacenter 位（默认 1/1，Java 版默认 0/0）避免同时运行时撞号。
"""

import threading
import time

_EPOCH = 1288834974657  # Twitter 雪花纪元，与 Hutool/MyBatis-Flex 一致

_WORKER_ID_BITS = 5
_DATACENTER_ID_BITS = 5
_SEQUENCE_BITS = 12

_MAX_WORKER_ID = -1 ^ (-1 << _WORKER_ID_BITS)
_MAX_DATACENTER_ID = -1 ^ (-1 << _DATACENTER_ID_BITS)
_SEQUENCE_MASK = -1 ^ (-1 << _SEQUENCE_BITS)

_WORKER_ID_SHIFT = _SEQUENCE_BITS
_DATACENTER_ID_SHIFT = _SEQUENCE_BITS + _WORKER_ID_BITS
_TIMESTAMP_SHIFT = _SEQUENCE_BITS + _WORKER_ID_BITS + _DATACENTER_ID_BITS


class SnowflakeGenerator:
    def __init__(self, worker_id: int = 1, datacenter_id: int = 1):
        if not 0 <= worker_id <= _MAX_WORKER_ID:
            raise ValueError(f"worker_id 必须在 0..{_MAX_WORKER_ID}")
        if not 0 <= datacenter_id <= _MAX_DATACENTER_ID:
            raise ValueError(f"datacenter_id 必须在 0..{_MAX_DATACENTER_ID}")
        self._worker_id = worker_id
        self._datacenter_id = datacenter_id
        self._sequence = 0
        self._last_ts = -1
        self._lock = threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            ts = int(time.time() * 1000)
            if ts < self._last_ts:
                # 时钟回拨：等到追上为止，宁可阻塞也不发重复 ID
                ts = self._wait_until(self._last_ts)
            if ts == self._last_ts:
                self._sequence = (self._sequence + 1) & _SEQUENCE_MASK
                if self._sequence == 0:  # 同毫秒内序列耗尽
                    ts = self._wait_until(self._last_ts)
            else:
                self._sequence = 0
            self._last_ts = ts
            return (
                ((ts - _EPOCH) << _TIMESTAMP_SHIFT)
                | (self._datacenter_id << _DATACENTER_ID_SHIFT)
                | (self._worker_id << _WORKER_ID_SHIFT)
                | self._sequence
            )

    @staticmethod
    def _wait_until(last_ts: int) -> int:
        ts = int(time.time() * 1000)
        while ts <= last_ts:
            time.sleep(0.0005)
            ts = int(time.time() * 1000)
        return ts


_generator = SnowflakeGenerator()


def next_id() -> int:
    return _generator.next_id()
