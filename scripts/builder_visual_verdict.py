#!/usr/bin/env python3
"""Compatibility wrapper for the packaged RVND visual-verdict interface."""
from rvnd.release.visual_verdict import main


if __name__ == "__main__":
    raise SystemExit(main())
