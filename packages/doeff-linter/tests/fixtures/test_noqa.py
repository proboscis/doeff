# Test noqa comments

import os


# This should NOT trigger because of noqa
def dict():  # noqa: DOEFF001
    return {}


# This should NOT trigger because of noqa (all rules)
def list():  # noqa
    return []


# This should still trigger DOEFF009 (no return type)
def set():  # noqa: DOEFF001
    return set()


# This SHOULD trigger - no noqa
api_key = os.environ["API_KEY"]


# This should NOT trigger - noqa for DOEFF004
db_url = os.environ["DB_URL"]  # noqa: DOEFF004

# This should NOT trigger - noqa:DOEFF004 (no space after colon)
cache_url = os.environ["CACHE_URL"]  # noqa:DOEFF004

# This should NOT trigger - lowercase rule ID
redis_url = os.environ["REDIS_URL"]  # noqa: doeff004

# This should NOT trigger - multiple rules, no spaces
queue_url = os.environ["QUEUE_URL"]  # noqa:DOEFF004,DOEFF009



