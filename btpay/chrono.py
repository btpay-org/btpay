#
# Time/data related functions. Trying hard not to rewrite existing things.
#
# Everything should be stored/calculated in UTC.
# - but it should also be marked as a specific timezone, like UTC.
#
import time, datetime, calendar, pendulum
from pendulum import UTC

def NOW():
    # Use this for everything.
    #return datetime.datetime.utcnow()
    return pendulum.now(UTC)

def TIME_AGO(**kws):
    # Return a time in past, like TIME_AGO(hours=3)
    return NOW() - datetime.timedelta(**kws)

def TIME_FUTURE(**kws):
    # Return a time in future, like TIME_FUTURE(hours=3)
    return NOW() + datetime.timedelta(**kws)

def as_time_t(dt):
    " convert datetime into unix timestamp (all UTC)"
    return calendar.timegm(dt.utctimetuple())

def from_time_t(time_t):
    " convert unix timestamp into datetime (all UTC)"
    #return datetime.datetime.utcfromtimestamp(float(time_t))
    return pendulum.from_timestamp(time_t)

# EOF
