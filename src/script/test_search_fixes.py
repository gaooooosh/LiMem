#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug script to diagnose search issues with time-based weight calculation."""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.config import DB_PATH, DECAY_RATE
from limem.db import init_db, open_connection

def check_database():
    """Check database events and their timestamps."""
    print("=" * 80)
    print("Database Diagnostic Report")
    print("=" * 80)

    conn = open_connection(DB_PATH)
    init_db(conn)

    # Get current time
    t_now = int(time.time())
    print(f"\n⏰ Current timestamp (t_now): {t_now}")
    print(f"   Current time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t_now))}")

    # Check DECAY_RATE
    print(f"\n⚙️  DECAY_RATE: {DECAY_RATE}")
    print(f"   Half-life: {0.693 / DECAY_RATE if DECAY_RATE > 0 else 'inf'} seconds")
    print(f"   Half-life: {0.693 / DECAY_RATE / 86400 if DECAY_RATE > 0 else 'inf'} days")

    # Query all events with their timestamps
    query = """
    MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
    RETURN e.id, e.summary, r.t_valid, r.t_created, r.c_valid, r.t_expired, r.t_invalid
    ORDER BY r.t_valid DESC
    """

    print("\n📊 Events in Database:")
    print("-" * 80)

    resp = conn.execute(query)
    events = []
    while resp.has_next():
        row = resp.get_next()
        events.append({
            "event_id": row[0],
            "summary": row[1],
            "t_valid": row[2],
            "t_created": row[3],
            "c_valid": row[4],
            "t_expired": row[5],
            "t_invalid": row[6],
        })

    if not events:
        print("❌ No events found in database!")
        return

    print(f"Found {len(events)} events\n")

    for i, event in enumerate(events, 1):
        t_valid = event["t_valid"] or 0
        time_diff = t_now - t_valid

        # Calculate weight using the formula
        import math
        base_weight = math.log(1 + (event["c_valid"] or 0))
        temporal_factor = math.exp(-DECAY_RATE * time_diff)
        weight = base_weight * temporal_factor

        print(f"[{i}] Event: {event['event_id']}")
        print(f"    Summary: {event['summary'][:60]}...")
        print(f"    t_valid: {t_valid} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t_valid)) if t_valid > 0 else 'N/A'})")
        print(f"    t_created: {event['t_created']}")
        print(f"    c_valid: {event['c_valid']}")
        print(f"    t_expired: {event['t_expired']}")
        print(f"    t_invalid: {event['t_invalid']}")
        print(f"    time_diff: {time_diff} seconds ({time_diff / 86400:.2f} days)")
        print(f"    base_weight: {base_weight:.6f}")
        print(f"    temporal_factor: {temporal_factor:.10f}")
        print(f"    final_weight: {weight:.10f}")
        print()

    # Check for issues
    print("\n🔍 Potential Issues:")
    print("-" * 80)

    # Check if all events have very old timestamps
    very_old_events = [e for e in events if (t_now - (e["t_valid"] or 0)) > 86400 * 365]  # > 1 year
    if very_old_events:
        print(f"⚠️  {len(very_old_events)} events have timestamps > 1 year old")
        print("   This will cause extremely low temporal factors")

    # Check if any events are expired/invalid
    expired_events = [e for e in events if e["t_expired"] is not None]
    invalid_events = [e for e in events if e["t_invalid"] is not None and t_now >= e["t_invalid"]]
    if expired_events:
        print(f"⚠️  {len(expired_events)} events are marked as expired")
    if invalid_events:
        print(f"⚠️  {len(invalid_events)} events are marked as invalid")

    # Check if DECAY_RATE is appropriate
    if DECAY_RATE > 1e-5:
        print(f"⚠️  DECAY_RATE ({DECAY_RATE}) might be too high")
        print(f"   With this rate, half-life is only {0.693 / DECAY_RATE / 3600:.2f} hours")

if __name__ == "__main__":
    check_database()
