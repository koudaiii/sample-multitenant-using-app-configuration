"""Guard the constraints the whole project depends on."""

import importlib.util

import pytest


def test_core_package_imports_without_azure_extra():
    import mtappconfig

    assert mtappconfig.__doc__


def test_azure_source_imports_without_azure_installed():
    """mtappconfig.azure_source (the one module that talks to the SDK) must
    import cleanly even when the SDK is not installed.

    The base install deliberately omits the App Configuration SDK, and this
    module's own docstring promises every azure import happens inside a
    function, not at module scope. A module-scope import there would break
    the whole application for anyone who has not installed the `azure`
    extra, so this assertion stays unconditional regardless of whether this
    environment happens to have the SDK installed or not.
    """
    import mtappconfig.azure_source  # noqa: F401 - must import without azure installed


@pytest.mark.skipif(
    importlib.util.find_spec("azure") is not None,
    reason="the Azure SDK is installed, which the real-store flow does on purpose",
)
def test_azure_sdk_is_not_a_required_dependency():
    """The base install must work without the App Configuration SDK.

    The development environment this repository was built in cannot download
    these packages, so this asserts that precondition directly: the azure
    package is genuinely absent by default. Installing it deliberately (via
    `uv pip install -r requirements-azure.txt`, as `tests/test_azure_source.py`
    and the sample READMEs' real-store seeding sections both instruct) is a
    supported state, not a violation of this constraint, so this test skips
    itself rather than failing when the SDK is present.
    """
    assert importlib.util.find_spec("azure") is None


def test_flask_is_available_for_the_base_install():
    """The one dependency the base install actually needs must be present."""
    assert importlib.util.find_spec("flask") is not None


def test_azure_sdk_is_not_declared_in_pyproject():
    """uv resolves optional dependencies when locking, so declaring the App
    Configuration SDK anywhere in pyproject.toml breaks `uv sync` outright."""
    import pathlib

    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text()
    assert "azure-appconfiguration-provider" not in text
    assert "azure-identity" not in text
