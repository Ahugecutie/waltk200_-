from threading import Lock
from typing import Dict, Any
from time import time

CACHE_LOCK = Lock()

GLOBAL_CACHE: Dict[str, Any] = {
    "snapshot": None,
    "detail": {},
    "updated_at": None,
    "status": "warming_up",  # warming_up | ready | error
}

