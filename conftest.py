"""Pytest configuration shared by the core tests and every sample."""

import pytest

import sys
from pathlib import Path

# Each sample directory holds its own pattern module. Adding them to sys.path
# lets both the sample's own tests and the cross-pattern contract test import
# them. Module names are unique per sample so nothing collides.
_SAMPLES = Path(__file__).parent / "samples"
if _SAMPLES.is_dir():
    for _sample_dir in sorted(p for p in _SAMPLES.iterdir() if p.is_dir()):
        sys.path.insert(0, str(_sample_dir))


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests that talk to a real Azure App Configuration store",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live and a real App Configuration store")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
