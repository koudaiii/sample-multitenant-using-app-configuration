"""Guard the constraints the whole project depends on."""

import importlib.util


def test_core_package_imports_without_azure_extra():
    import mtappconfig

    assert mtappconfig.__doc__


def test_azure_sdk_is_not_a_required_dependency():
    """The base install must work without the App Configuration SDK.

    The development environment cannot download these packages, so anything
    that imports them at module scope would break the entire test suite.
    """
    assert importlib.util.find_spec("flask") is not None


def test_azure_sdk_is_not_declared_in_pyproject():
    """uv resolves optional dependencies when locking, so declaring the App
    Configuration SDK anywhere in pyproject.toml breaks `uv sync` outright."""
    import pathlib

    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert "azure-appconfiguration-provider" not in pyproject.read_text()
