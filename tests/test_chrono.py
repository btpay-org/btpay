#
# Tests for chrono utilities
#
import datetime
import pendulum
from btpay.chrono import NOW, TIME_AGO, TIME_FUTURE, as_time_t, from_time_t


def test_now():
    before = pendulum.now(pendulum.UTC)
    n = NOW()
    after = pendulum.now(pendulum.UTC)
    assert before <= n <= after


def test_time_ago():
    n = NOW()
    ago = TIME_AGO(hours=1)
    diff = n - ago
    assert 3599 <= diff.total_seconds() <= 3601


def test_time_future():
    n = NOW()
    future = TIME_FUTURE(hours=1)
    diff = future - n
    assert 3599 <= diff.total_seconds() <= 3601


def test_time_t_roundtrip():
    dt = datetime.datetime(2024, 6, 15, 12, 30, 0)
    t = as_time_t(dt)
    assert isinstance(t, int)
    dt2 = from_time_t(t)
    assert dt2.year == 2024
    assert dt2.month == 6
    assert dt2.day == 15
    assert dt2.hour == 12
    assert dt2.minute == 30

# EOF
