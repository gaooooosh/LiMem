# -*- coding: utf-8 -*-
import hashlib
from datetime import datetime


def hash_summary(summary):
    # Event ID is a deterministic hash of its summary (as specified).
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


def parse_time_to_unix(ts):
    # Simple parser for example.json timestamps (YYYY-MM-DD HH:MM:SS).
    return int(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp())
