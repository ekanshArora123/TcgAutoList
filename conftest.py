"""Pytest session setup.

Loads `.env` so local test runs (notably the `external` suite) pick up optional
secrets like TCGPLAYER_AUTH_COOKIE the same way the app does via main.py.

In CI there is no `.env`; secrets come from pipeline variables mapped to env in
azure-pipelines.yml, and load_dotenv() is a harmless no-op there. It never
overrides variables already set in the environment, so CI-provided values win.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()
