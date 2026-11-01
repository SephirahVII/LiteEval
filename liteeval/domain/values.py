"""Pure canonical values shared by domain and persistence."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def decoded(value):
    def invalid(constant):
        raise ValueError('Nonfinite JSON number: ' + constant)
    return json.loads(value, parse_constant=invalid)



def pagination(limit=50, offset=0):
    limit, offset = int(limit), int(offset)
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError('Pagination limit must be 1..200; offset >= 0')
    return limit, offset
