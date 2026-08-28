"""Guard the constraints the whole project depends on."""

import importlib.util


def test_core_package_imports_without_azure_extra():
    import mtappconfig

    assert mtappconfig.__doc__


def test_azure_sdk_is_not_a_required_dependency():
    """The base install must work without the App Configuration SDK.

    The development environment cannot download these packages, so anything
    that imports them at module scope would break the entire test suite. This
    asserts the actual precondition: the azure package is genuinely absent
    here, and mtappconfig.azure_source (the one module that talks to it)
    still imports cleanly, because its docstring promises every azure import
    happens inside a function, not at module scope.
    """
    assert importlib.util.find_spec("azure") is None

    import mtappconfig.azure_source  # noqa: F401 - must import without azure installed


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
