#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Daily Low-Buy Dragon - Main Entry Point
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))
from scan_daily_low_buy import run_scanner

if __name__ == '__main__':
    run_scanner()
