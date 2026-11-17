"""Offline prototype layer: simple-algorithm stand-ins for the RNAQwen family.

The public contract (see PLAN.md §3/§4) is kept here so that swapping in a real
model later does not change the caller. All scores are uncalibrated heuristics.
"""
from .scoring import seed_score, scan_windows, WINDOW_NT

__all__ = ["seed_score", "scan_windows", "WINDOW_NT", "PROTO_VERSION"]

PROTO_VERSION = "proto-0.3.0-adapter"
