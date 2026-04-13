#!/usr/bin/env python3
"""
flash_cache_utils.py — **disabled**
This module is intentionally a no-op so callers can import safely while flash
cache is retired. All functions below do nothing and return empty strings.
"""

def write_flash_line(agent: str, line: str):
    return None

def read_flash_cache(agent: str) -> str:
    return ""

def finalize_flash_thought(agent: str) -> str:
    return ""

def clear_flash_cache(agent: str):
    return None



