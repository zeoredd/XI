#!/usr/bin/env python3
"""🕰️ time_utils.py
Core time handling module for XI agents and systems.
Implements 4-4-5 business calendar with leap-year awareness.

Functions:
- is_leap_year(year)
- get_day_of_year(date)
- get_calendar_ranges(year)
- get_current_quarter_and_period(date)
"""

from datetime import date


def is_leap_year(year):
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)

def get_day_of_year(target_date):
    return target_date.timetuple().tm_yday

def get_calendar_ranges(year):
    total_days = 366 if is_leap_year(year) else 365

    return {
        'Q1': {'start_day': 1, 'end_day': 91, 'periods': [
            {'name': 'P1', 'start_day': 1, 'end_day': 28},
            {'name': 'P2', 'start_day': 29, 'end_day': 56},
            {'name': 'P3', 'start_day': 57, 'end_day': 91},
        ]},
        'Q2': {'start_day': 92, 'end_day': 182, 'periods': [
            {'name': 'P1', 'start_day': 92, 'end_day': 119},
            {'name': 'P2', 'start_day': 120, 'end_day': 147},
            {'name': 'P3', 'start_day': 148, 'end_day': 182},
        ]},
        'Q3': {'start_day': 183, 'end_day': 273, 'periods': [
            {'name': 'P1', 'start_day': 183, 'end_day': 210},
            {'name': 'P2', 'start_day': 211, 'end_day': 238},
            {'name': 'P3', 'start_day': 239, 'end_day': 273},
        ]},
        'Q4': {'start_day': 274, 'end_day': total_days, 'periods': [
            {'name': 'P1', 'start_day': 274, 'end_day': 301},
            {'name': 'P2', 'start_day': 302, 'end_day': 329},
            {'name': 'P3', 'start_day': 330, 'end_day': total_days},
        ]},
    }

def get_current_quarter_and_period(target_date):
    year = target_date.year
    day = get_day_of_year(target_date)
    calendar = get_calendar_ranges(year)

    for q_name, q_data in calendar.items():
        if q_data['start_day'] <= day <= q_data['end_day']:
            for period in q_data['periods']:
                if period['start_day'] <= day <= period['end_day']:
                    return {
                        'quarter': q_name,
                        'period': period['name'],
                        'day_of_year': day,
                        'year': year
                    }
    return None

import datetime as dt

def parse_iso(ts: str | None):
    """Parse ISO8601 or YYYY-MM-DD with optional Z. Returns aware datetime or None."""
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None

def is_recent(ts: str | None, hours: int | None) -> bool:
    """True if timestamp is within N hours of now. 
    If hours is None → always True. 
    If ts invalid → True (lenient)."""
    if hours is None:
        return True
    tdt = parse_iso(ts)
    if not tdt:
        return True  # flip to False for strict mode
    age_h = (dt.datetime.now(dt.timezone.utc) - tdt.astimezone(dt.timezone.utc)).total_seconds() / 3600.0
    return age_h <= hours

