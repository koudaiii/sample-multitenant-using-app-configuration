"""Guard the constraints the whole project depends on."""

from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYTHON_VERSION = REPO_ROOT / ".python-version"
FOUNDATION_DOCS = [
    REPO_ROOT / "docs/superpowers/specs/2026-08-28-multitenant-app-configuration-design.md",
    REPO_ROOT / "docs/superpowers/plans/2026-08-28-multitenant-app-configuration.md",
]


def _dependency_config() -> list[tuple[str, str]]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)

    dependency_config = [
        *(("project.dependencies", dependency) for dependency in data["project"]["dependencies"]),
    ]
    for extra, dependencies in data["project"].get("optional-dependencies", {}).items():
        dependency_config.extend(
            (f"project.optional-dependencies.{extra}", dependency) for dependency in dependencies
        )
    for group, dependencies in data.get("dependency-groups", {}).items():
        dependency_config.extend((f"dependency-groups.{group}", dependency) for dependency in dependencies)

    return dependency_config


def _requirement_name(requirement: str) -> str:
    name = requirement.split(";", 1)[0].strip()
    for separator in ("[", " ", "<", ">", "=", "!", "~"):
        name = name.split(separator, 1)[0]
    return name.lower()

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


def test_azure_sdk_is_not_a_required_dependency():
    """The base install must keep Azure SDK packages out of required deps."""
    dependency_config = _dependency_config()

    assert "flask" in [
        dependency for section, dependency in dependency_config if section == "project.dependencies"
    ]

    offending_dependencies = [
        f"{section}: {dependency}"
        for section, dependency in dependency_config
        if _requirement_name(dependency)
        in {"azure-appconfiguration-provider", "azure-identity"}
    ]

    assert not offending_dependencies, (
        "Forbidden Azure SDK dependencies must stay out of pyproject.toml dependency configuration:\n"
        + "\n".join(offending_dependencies)
    )


def test_foundation_docs_match_the_tracked_python_version_contract():
    """The shipped docs must agree with the tracked interpreter pin."""
    version = PYTHON_VERSION.read_text().strip()

    assert version == "3.14.3"

    for doc in FOUNDATION_DOCS:
        text = doc.read_text()
        assert ".python-version" in text
        assert version in text
        assert ".python-version` を Git 管理" in text
        forbidden_phrase = "git " + "管理しない"
        assert forbidden_phrase not in text
