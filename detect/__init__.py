"""Detect package initializer.

Makes `detect` a proper Python package so that relative imports
like `from .errors import CollectError` work when running via
`python -m detect.collect_playwright`.
"""

