import time

import pytest

from trading_system.ratelimit import TokenBucket


def test_burst_is_instant():
    bucket = TokenBucket(rate_per_sec=5, burst=3)
    start = time.monotonic()
    for _ in range(3):
        bucket.acquire()
    assert time.monotonic() - start < 0.05


def test_throttles_beyond_burst():
    bucket = TokenBucket(rate_per_sec=50, burst=1)
    bucket.acquire()  # drain the bucket
    start = time.monotonic()
    bucket.acquire()  # must wait ~1/50s for a refill
    assert time.monotonic() - start >= 0.015


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        TokenBucket(rate_per_sec=0, burst=1)
    with pytest.raises(ValueError):
        TokenBucket(rate_per_sec=1, burst=0)
