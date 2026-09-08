"""Guard the constraints the whole project depends on."""

from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYTHON_VERSION = REPO_ROOT / ".python-version"
AZURE_REQUIREMENTS = REPO_ROOT / "requirements-azure.txt"
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


def test_azure_provider_floor_supports_refresh_enabled():
    requirements = {
        line.strip()
        for line in AZURE_REQUIREMENTS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert "azure-appconfiguration-provider>=2.5.0" in requirements


def test_uv_index_configuration_remains_user_managed():
    config = tomllib.loads(PYPROJECT.read_text())
    settings = config["tool"]["uv"]
    assert not settings.get("index")
    for key in ("default-index", "index-url", "extra-index-url"):
        assert key not in settings


def test_uv_lock_artifact_urls_do_not_embed_credentials():
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    registry_packages = [package for package in lock["package"] if "registry" in package["source"]]

    assert registry_packages
    for package in registry_packages:
        artifacts = [*package.get("wheels", [])]
        if "sdist" in package:
            artifacts.append(package["sdist"])
        for artifact in artifacts:
            url = urlsplit(artifact["url"])
            assert url.scheme == "https"
            assert url.username is None and url.password is None
            assert not url.query and not url.fragment


def test_python_source_guidance_uses_uv_configuration_and_supported_commands():
    text = (REPO_ROOT / "README.md").read_text()
    assert "uv.toml" in text
    assert "[[index]]" in text
    assert "[[tool.uv.index]]" in text
    assert "uv lock --check" in text
    assert "uv sync --verbose" in text
    assert "pip の設定" in text


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


def test_current_readmes_use_configured_nuget_feeds_without_cache_workarounds():
    for path in (REPO_ROOT / "README.md", REPO_ROOT / "samples/05-dotnet-cache-refresh/README.md"):
        text = path.read_text()
        assert "nuget.config" in text.lower()
        assert "dotnet nuget list source" in text
        assert "既存キャッシュを前提としません" in text
        assert "~/.nuget/packages" not in text
        assert not re.search(r"dotnet\s+(?:restore|build)\s+--source", text)


def test_user_readmes_exclude_development_environment_and_verification_history():
    paths = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "samples").glob("*/README.md"))]
    for path in paths:
        text = path.read_text()
        for phrase in (
            "この開発環境",
            "この環境では",
            "環境で開発されました",
            "過去の作業",
            "azure-default",
            "確認しました",
            "復元を確認しています",
            "検証済み",
            "実行されていません",
            "実行検証していません",
            "検証していません",
            "最終レビューで",
        ):
            assert phrase not in text, f"{path.relative_to(REPO_ROOT)}: {phrase}"
        assert not re.search(r"/(?:Users|home)/[^\s)`]+", text)


def test_dotnet_design_limits_cache_workaround_to_explicit_history():
    text = (REPO_ROOT / "docs/superpowers/specs/2026-09-06-dotnet-cache-refresh-sample-design.md").read_text()
    marker = "### 当初の事前検証(スパイク)の記録"
    assert marker in text
    before, rest = text.split(marker, 1)
    history, after = rest.split("### スコープ内", 1)
    assert "現在の復元手順ではありません" in history
    assert "~/.nuget/packages" not in before + after
    assert not re.search(r"dotnet\s+(?:restore|build)\s+--source", before + after)
    acceptance = after.split("## 7. 受け入れ条件", 1)[1]
    assert "nuget.config" in acceptance.lower()
    assert "キャッシュへの依存" not in acceptance


def test_dotnet_plan_does_not_prescribe_local_cache_source_overrides():
    text = (REPO_ROOT / "docs/superpowers/plans/2026-09-06-dotnet-cache-refresh-sample-plan.md").read_text()
    assert "nuget.config" in text.lower()
    assert ".nuget/packages" not in text
    assert not re.search(r"dotnet\s+(?:restore|build)\s+--source", text)
    assert not re.search(r"/(?:Users|home)/[^\s)`]+", text)
