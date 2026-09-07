# マルチテナント × Azure App Configuration サンプル 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Microsoft Learn の「Multitenancy and Azure App Configuration」が示す3つの分離モデルを、動かして違いが分かる Flask サンプル3本として実装する。

**Architecture:** 共通コア `src/mtappconfig/` に「テナント ID 検証・設定ソース抽象・TTL + LRU キャッシュ・Flask app factory・ローカルフェイクストア」を置き、`samples/<pattern>/source.py` にパターン固有の設定取得方法だけを実装する。3本を diff するとパターンの差分だけが残る。実 Azure への束縛は `azure_source.py` 1ファイルに隔離する。

**Tech Stack:** Python 3.14.3 / uv / Flask / pytest / Bicep。App Configuration の SDK（`azure-appconfiguration-provider`, `azure-identity`）はオプションのエクストラ。

**Spec:** `docs/superpowers/specs/2026-08-28-multitenant-app-configuration-design.md`

## Global Constraints

- Python は `3.14.3`（`.python-version` を Git 管理して固定済み）。
- パッケージ管理は `uv`。基本依存は `flask` のみ。`pytest` は `dev` 依存グループ。
- **すべての uv コマンドは `--offline` を付けて実行する**（`uv sync --offline`、`uv run --offline pytest`）。この環境は PyPI のファイル配信ホストに到達できず、`--offline` なしでは flask の推移依存すら取得できない。README に書くユーザー向け手順は `--offline` なしの通常形とし、制約は「既知の制約」節に記載する。
- **`azure-appconfiguration-provider` と `azure-identity` を `pyproject.toml` に書いてはならない。** `uv lock` は optional-dependencies も含めて依存グラフ全体を解決するため、宣言するだけで `uv sync` が失敗する。Azure SDK は `requirements-azure.txt` に分離し、README で個別インストールを案内する。
- **プロジェクトをビルド可能なパッケージにしない。** `hatchling` がキャッシュに無くビルドできないため、`[build-system]` は書かず `[tool.uv] package = false` とし、`mtappconfig` は pytest の `pythonpath = ["src"]` とサンプル側の明示的な `sys.path` 追加で解決する。
- **`azure` エクストラなしで、コア・3サンプル・全テストが動作し `pytest` が全通しすること。** これが各タスクの受け入れ条件に暗黙に含まれる。
- `src/mtappconfig/azure_source.py` の `azure.*` import は必ず関数内の遅延 import にする。モジュールの import 自体はエクストラ未インストールでも成功しなければならない。
- 実 Azure に触れるテストは `@pytest.mark.live` を付ける。既定でスキップされること。
- Azure 認証は `DefaultAzureCredential` のみ。接続文字列・アクセスキーをコードにも Bicep にも書かない。
- テナント ID の正規表現は `\A[a-z0-9][a-z0-9-]{1,30}[a-z0-9]\Z`（英小文字・数字・ハイフン、3〜32文字、先頭末尾は英数字）。**`^`/`$` は使わないこと** — Python の `$` は末尾の改行の直前にもマッチするため、`"tenant-a\n"` を通してしまう。
- App Configuration Data Reader のロール定義 ID は `516239f1-63e1-4d78-a4de-a74fb236a071`。
- コード内のコメントと docstring は英語、README と計画・設計文書は日本語。

## File Structure

| ファイル | 責務 |
| --- | --- |
| `pyproject.toml` | 依存定義、pytest 設定、`live` マーカー登録。Azure SDK は書かない |
| `requirements-azure.txt` | 実 Azure に繋ぐときだけ入れる SDK |
| `src/mtappconfig/tenants.py` | テナント ID の検証とレジストリ。信頼できない入力の唯一の関門 |
| `src/mtappconfig/source.py` | `TenantConfig` / `TenantConfigSource` / `ConfigStoreUnavailableError` |
| `src/mtappconfig/fake.py` | ローカルフェイク App Configuration ストア（key/label/trim と障害注入） |
| `src/mtappconfig/sampledata.py` | 3パターン共通の正準サンプルデータ |
| `src/mtappconfig/observability.py` | JSON 構造化ログ |
| `src/mtappconfig/cache.py` | TTL + LRU のテナント別キャッシュ、統計、劣化運転 |
| `src/mtappconfig/webapp.py` | Flask app factory とルート |
| `src/mtappconfig/azure_source.py` | 実 provider への束縛（遅延 import、この環境では未検証） |
| `samples/01-shared-store-key-prefix/` | キープレフィックス方式 |
| `samples/02-shared-store-label/` | ラベル方式 |
| `samples/03-store-per-tenant/` | テナント別ストア方式 |
| `tests/` | コアと契約のテスト |
| `infra/modules/` | 共通 Bicep モジュール |

`sampledata.py` は設計書 3.1 のファイル一覧にない追加ファイル。3パターンが「同じデータを別レイアウトで格納する」ことを契約テストで示すため、データ本体を1箇所に置く必要がある。

---

### Task 1: プロジェクト初期化

**Files:**
- Create: `pyproject.toml`
- Create: `src/mtappconfig/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_project_setup.py`

**Interfaces:**
- Consumes: なし
- Produces: `uv run pytest` が実行できる状態。パッケージ `mtappconfig` が import 可能。

- [ ] **Step 1: `pyproject.toml` を作成**

```toml
[project]
name = "mtappconfig-samples"
version = "0.1.0"
description = "Multitenancy patterns for Azure App Configuration, as runnable Flask samples"
requires-python = ">=3.14"
dependencies = ["flask"]

[dependency-groups]
dev = ["pytest"]

# Not a distributable package: the App Configuration samples are run in place.
# Keeping it virtual also avoids needing a build backend, which this
# environment cannot install.
[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests", "samples"]
pythonpath = ["src"]
markers = [
    "live: touches a real Azure App Configuration store; skipped unless --run-live is passed",
]
```

**Azure SDK を `pyproject.toml` に書かないこと。** `uv lock` は optional-dependencies も
解決対象に含めるため、取得できないパッケージを宣言すると `uv sync` 自体が失敗する。
代わりに次のファイルを作る。

Create: `requirements-azure.txt`

```
# Needed only to talk to a real Azure App Configuration store.
# Kept out of pyproject.toml on purpose: uv resolves optional dependencies when
# locking, so declaring these would break `uv sync` for everyone who only wants
# to run the samples against the in-memory fake.
#   uv pip install -r requirements-azure.txt
azure-appconfiguration-provider>=2.0
azure-identity>=1.17
```

- [ ] **Step 2: パッケージの空ファイルを作成**

```bash
mkdir -p src/mtappconfig tests
printf '"""Multitenancy patterns for Azure App Configuration."""\n' > src/mtappconfig/__init__.py
touch tests/__init__.py
```

- [ ] **Step 3: `live` マーカーを既定でスキップする conftest を作成**

`tests/../conftest.py` ではなくリポジトリ直下の `conftest.py` に置く（`samples/` 配下のテストからも効かせるため）。

Create: `conftest.py`

```python
"""Pytest configuration shared by the core tests and every sample."""

import pytest


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
```

- [ ] **Step 4: セットアップを検証するテストを書く**

Create: `tests/test_project_setup.py`

```python
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
```

- [ ] **Step 5: 同期してテストを実行**

```bash
uv sync --offline
uv run --offline pytest tests/test_project_setup.py -q
```

Expected: 3 passed。`samples/` がまだ存在しなくても `testpaths` に書いてある件で pytest は
エラーにならない（検証済み）。

- [ ] **Step 6: コミット**

```bash
git add pyproject.toml uv.lock requirements-azure.txt conftest.py src/mtappconfig/__init__.py tests/__init__.py tests/test_project_setup.py
git commit -m "chore: scaffold project with the Azure SDK kept out of the lockfile"
```

---

### Task 2: テナント ID の検証とレジストリ

**Files:**
- Create: `src/mtappconfig/tenants.py`
- Test: `tests/test_tenants.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `TENANT_ID_PATTERN: re.Pattern[str]`
  - `Tenant(tenant_id: str, display_name: str)` — frozen dataclass
  - `UnknownTenantError(LookupError)`
  - `TenantRegistry(tenants: Iterable[Tenant])` — `.resolve(raw: str) -> Tenant`, `.ids() -> list[str]`, `__iter__`, `__len__`

これはサンプル全体で最も重要なセキュリティ境界。信頼できないテナント ID が設定ストアのクエリに到達する唯一の経路をここで塞ぐ。

- [ ] **Step 1: 失敗するテストを書く**

Create: `tests/test_tenants.py`

```python
"""Tenant identifier validation is the security boundary of these samples."""

import pytest

from mtappconfig.tenants import Tenant, TenantRegistry, UnknownTenantError


@pytest.fixture
def registry():
    return TenantRegistry(
        [
            Tenant(tenant_id="tenant-a", display_name="Tenant A"),
            Tenant(tenant_id="tenant-b", display_name="Tenant B"),
        ]
    )


def test_resolves_a_registered_tenant(registry):
    assert registry.resolve("tenant-a").display_name == "Tenant A"


def test_lists_registered_ids(registry):
    assert registry.ids() == ["tenant-a", "tenant-b"]


def test_len_and_iteration(registry):
    assert len(registry) == 2
    assert [t.tenant_id for t in registry] == ["tenant-a", "tenant-b"]


@pytest.mark.parametrize(
    "hostile",
    [
        "*",                # would match every tenant's keys behind a prefix filter
        "tenant-a/*",
        "tenant-a/../tenant-b",
        "../tenant-b",
        "tenant-a/LogLevel",
        "TENANT-A",         # case is significant
        "tenant a",
        "",
        "ab",               # shorter than the 3 character minimum
        "a" * 33,           # longer than the 32 character maximum
        "-tenant-a",        # must not start with a hyphen
        "tenant-a-",        # must not end with a hyphen
        "tenant-a\n",       # a trailing newline must not slip past the anchor
    ],
)
def test_rejects_malformed_ids(registry, hostile):
    with pytest.raises(UnknownTenantError):
        registry.resolve(hostile)


def test_rejects_well_formed_but_unregistered_id(registry):
    with pytest.raises(UnknownTenantError):
        registry.resolve("tenant-c")


def test_registry_rejects_a_malformed_id_at_construction():
    with pytest.raises(ValueError):
        TenantRegistry([Tenant(tenant_id="not valid", display_name="x")])
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest tests/test_tenants.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtappconfig.tenants'`

- [ ] **Step 3: 最小の実装を書く**

Create: `src/mtappconfig/tenants.py`

```python
"""Tenant registry and identifier validation.

Every untrusted tenant identifier must pass through `TenantRegistry.resolve`
before it reaches a configuration store. A raw identifier interpolated into a
key filter would let a caller read another tenant's settings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

TENANT_ID_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9-]{1,30}[a-z0-9]\Z")


class UnknownTenantError(LookupError):
    """The identifier is malformed, or names no registered tenant."""


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    display_name: str


class TenantRegistry:
    """The set of tenants this deployment serves."""

    def __init__(self, tenants: Iterable[Tenant]) -> None:
        self._tenants: dict[str, Tenant] = {}
        for tenant in tenants:
            if not TENANT_ID_PATTERN.match(tenant.tenant_id):
                raise ValueError(f"invalid tenant id in registry: {tenant.tenant_id!r}")
            self._tenants[tenant.tenant_id] = tenant

    def __iter__(self) -> Iterator[Tenant]:
        return iter(self._tenants.values())

    def __len__(self) -> int:
        return len(self._tenants)

    def ids(self) -> list[str]:
        return list(self._tenants)

    def resolve(self, raw: str) -> Tenant:
        """Validate an untrusted identifier and return the registered tenant.

        The pattern check is redundant with the registry lookup on its own, but
        both are kept: the pattern documents the contract, and a future source
        that builds queries from the identifier stays safe by construction.
        """
        if not isinstance(raw, str) or not TENANT_ID_PATTERN.match(raw):
            raise UnknownTenantError(raw)
        try:
            return self._tenants[raw]
        except KeyError:
            raise UnknownTenantError(raw) from None
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run --offline pytest tests/test_tenants.py -v`
Expected: PASS（17件）

- [ ] **Step 5: コミット**

```bash
git add src/mtappconfig/tenants.py tests/test_tenants.py
git commit -m "feat: add tenant registry with strict identifier validation"
```

---

### Task 3: 設定ソースの抽象

**Files:**
- Create: `src/mtappconfig/source.py`
- Test: `tests/test_source.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `ConfigStoreUnavailableError(RuntimeError)`
  - `TenantConfig(tenant_id: str, values: Mapping[str, str], reload: Callable[[], Mapping[str, str]] | None = None)` — `.refresh() -> bool`
  - `TenantConfigSource(Protocol)` — 属性 `name: str`、メソッド `load(tenant_id: str) -> TenantConfig`、`ping() -> None`

`refresh()` は「値が変わったら True」。呼び出し間隔の制御は Task 6 のキャッシュ側が持つ。

- [ ] **Step 1: 失敗するテストを書く**

Create: `tests/test_source.py`

```python
"""The single abstraction every pattern implements."""

from mtappconfig.source import TenantConfig


def test_refresh_without_a_reload_hook_reports_no_change():
    config = TenantConfig(tenant_id="tenant-a", values={"LogLevel": "Warning"})

    assert config.refresh() is False
    assert config.values == {"LogLevel": "Warning"}


def test_refresh_reports_a_change_and_swaps_the_values():
    versions = iter([{"LogLevel": "Debug"}])
    config = TenantConfig(
        tenant_id="tenant-a",
        values={"LogLevel": "Warning"},
        reload=lambda: next(versions),
    )

    assert config.refresh() is True
    assert config.values == {"LogLevel": "Debug"}


def test_refresh_reports_no_change_when_the_values_are_identical():
    config = TenantConfig(
        tenant_id="tenant-a",
        values={"LogLevel": "Warning"},
        reload=lambda: {"LogLevel": "Warning"},
    )

    assert config.refresh() is False
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest tests/test_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtappconfig.source'`

- [ ] **Step 3: 最小の実装を書く**

Create: `src/mtappconfig/source.py`

```python
"""The abstraction that every tenancy pattern implements.

Keeping this to one protocol is what lets the three samples share a Flask
application, a cache, and a test suite, so that the only thing that differs
between them is how a tenant's settings are selected from a store.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class ConfigStoreUnavailableError(RuntimeError):
    """The backing configuration store could not be reached."""


@dataclass
class TenantConfig:
    """One tenant's resolved settings, plus a way to reload them."""

    tenant_id: str
    values: Mapping[str, str]
    reload: Callable[[], Mapping[str, str]] | None = field(default=None, repr=False)

    def refresh(self) -> bool:
        """Reload from the store. Returns True when a value actually changed.

        Callers are expected to rate-limit this; see `TenantConfigCache`.
        """
        if self.reload is None:
            return False
        latest = self.reload()
        if latest == self.values:
            return False
        self.values = latest
        return True


@runtime_checkable
class TenantConfigSource(Protocol):
    """Selects one tenant's settings out of one or more stores."""

    name: str

    def load(self, tenant_id: str) -> TenantConfig:
        """Load settings for an already-validated tenant identifier."""
        ...

    def ping(self) -> None:
        """Raise ConfigStoreUnavailableError if the store is unreachable."""
        ...
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run --offline pytest tests/test_source.py -v`
Expected: PASS（3件）

- [ ] **Step 5: コミット**

```bash
git add src/mtappconfig/source.py tests/test_source.py
git commit -m "feat: add the tenant config source abstraction"
```

---

### Task 4: ローカルフェイクストアと正準サンプルデータ

**Files:**
- Create: `src/mtappconfig/fake.py`
- Create: `src/mtappconfig/sampledata.py`
- Test: `tests/test_fake.py`

**Interfaces:**
- Consumes: `mtappconfig.source.ConfigStoreUnavailableError`, `mtappconfig.tenants.Tenant`
- Produces:
  - `FakeSetting(key: str, value: str, label: str | None = None)`
  - `FakeAppConfigurationStore(name: str = "fake")` — `.set(key, value, label=None)`, `.select(key_filter="*", label_filter=None, trim_prefixes=()) -> dict[str, str]`, `.ping() -> None`、属性 `.request_count: int`、`.unavailable: bool`
  - `sampledata.TENANTS: list[Tenant]`、`sampledata.SHARED_SETTINGS: dict[str, str]`、`sampledata.TENANT_SETTINGS: dict[str, dict[str, str]]`、`sampledata.expected_config(tenant_id) -> dict[str, str]`

フェイクは App Configuration のクエリ意味論を忠実に模す。とくに **`label_filter=None` はラベルなしの設定だけに一致する**（実サービスの既定と同じ）。この挙動がサンプル02の教材価値の土台になる。

- [ ] **Step 1: 失敗するテストを書く**

Create: `tests/test_fake.py`

```python
"""The fake store must mimic App Configuration's query semantics closely
enough that the three patterns are exercised for real."""

import pytest

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.source import ConfigStoreUnavailableError


@pytest.fixture
def store():
    store = FakeAppConfigurationStore()
    store.set("shared/App:Version", "1.4.2")
    store.set("tenant-a/LogLevel", "Warning")
    store.set("tenant-b/LogLevel", "Debug")
    store.set("LogLevel", "Information", label="tenant-a")
    store.set("App:Version", "0.9.0", label="preview")
    return store


def test_prefix_filter_selects_only_that_prefix(store):
    assert store.select(key_filter="tenant-a/*") == {"tenant-a/LogLevel": "Warning"}


def test_trim_prefixes_strips_the_tenant_prefix(store):
    selected = store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"])

    assert selected == {"LogLevel": "Warning"}


def test_star_filter_selects_every_unlabelled_setting(store):
    assert store.select(key_filter="*") == {
        "shared/App:Version": "1.4.2",
        "tenant-a/LogLevel": "Warning",
        "tenant-b/LogLevel": "Debug",
    }


def test_label_filter_none_excludes_labelled_settings(store):
    """This is App Configuration's default and the reason sample 02 must
    always pass a label filter explicitly."""
    assert "LogLevel" not in store.select(key_filter="LogLevel")


def test_label_filter_selects_that_label(store):
    assert store.select(key_filter="*", label_filter="tenant-a") == {"LogLevel": "Information"}


def test_label_filter_star_selects_every_label(store):
    assert len(store.select(key_filter="*", label_filter="*")) == 5


def test_exact_key_filter(store):
    assert store.select(key_filter="tenant-b/LogLevel") == {"tenant-b/LogLevel": "Debug"}


def test_set_overwrites_the_same_key_and_label(store):
    store.set("tenant-a/LogLevel", "Error")

    assert store.select(key_filter="tenant-a/*") == {"tenant-a/LogLevel": "Error"}


def test_counts_requests_so_caching_can_be_observed(store):
    before = store.request_count
    store.select(key_filter="*")

    assert store.request_count == before + 1


def test_unavailable_store_raises_on_select(store):
    store.unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        store.select(key_filter="*")


def test_unavailable_store_raises_on_ping(store):
    store.unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        store.ping()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest tests/test_fake.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtappconfig.fake'`

- [ ] **Step 3: フェイクストアを実装**

Create: `src/mtappconfig/fake.py`

```python
"""An in-memory stand-in for one App Configuration store.

It exists so that every pattern, the cache, and the whole test suite run
without an Azure subscription, and so that failure modes (an unreachable
store) can be exercised deliberately.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .source import ConfigStoreUnavailableError


@dataclass(frozen=True)
class FakeSetting:
    key: str
    value: str
    label: str | None = None


def _matches_key(key_filter: str, key: str) -> bool:
    """App Configuration supports an exact key or a trailing '*' wildcard."""
    if key_filter == "*":
        return True
    if key_filter.endswith("*"):
        return key.startswith(key_filter[:-1])
    return key == key_filter


def _matches_label(label_filter: str | None, label: str | None) -> bool:
    """A None filter selects only unlabelled settings, matching the service."""
    if label_filter == "*":
        return True
    return label == label_filter


class FakeAppConfigurationStore:
    """One store. Sample 03 creates several of these."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.request_count = 0
        self.unavailable = False
        self._settings: dict[tuple[str, str | None], FakeSetting] = {}

    def set(self, key: str, value: str, label: str | None = None) -> None:
        self._settings[(key, label)] = FakeSetting(key=key, value=value, label=label)

    def set_many(self, settings: Iterable[FakeSetting]) -> None:
        for setting in settings:
            self.set(setting.key, setting.value, setting.label)

    def ping(self) -> None:
        if self.unavailable:
            raise ConfigStoreUnavailableError(f"fake store {self.name!r} is unavailable")

    def select(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> dict[str, str]:
        self.ping()
        self.request_count += 1
        selected: dict[str, str] = {}
        for setting in self._settings.values():
            if not _matches_key(key_filter, setting.key):
                continue
            if not _matches_label(label_filter, setting.label):
                continue
            key = setting.key
            for prefix in trim_prefixes:
                if key.startswith(prefix):
                    key = key[len(prefix) :]
                    break
            selected[key] = setting.value
        return selected
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run --offline pytest tests/test_fake.py -v`
Expected: PASS（11件）

- [ ] **Step 5: 正準サンプルデータを作成**

Create: `src/mtappconfig/sampledata.py`

```python
"""The one set of settings all three samples serve.

Each pattern stores this same data in a different layout; the contract test
asserts that all three resolve it back to identical results.
"""

from __future__ import annotations

from .tenants import Tenant

TENANTS: list[Tenant] = [
    Tenant(tenant_id="tenant-a", display_name="Tenant A"),
    Tenant(tenant_id="tenant-b", display_name="Tenant B"),
]

# Settings that apply to every tenant. Keeping them in one place is the point
# the guidance makes about shared settings: one value, one place to update.
SHARED_SETTINGS: dict[str, str] = {
    "App:SupportEmail": "support@contoso.example",
    "App:Version": "1.4.2",
}

# Per-tenant settings. The guidance names exactly these uses: a tenant's
# database name, and a per-tenant log level for diagnosing one tenant's issue.
TENANT_SETTINGS: dict[str, dict[str, str]] = {
    "tenant-a": {
        "DisplayName": "Tenant A",
        "LogLevel": "Warning",
        "DatabaseName": "db-tenant-a",
        "Features:BetaDashboard": "false",
    },
    "tenant-b": {
        "DisplayName": "Tenant B",
        "LogLevel": "Debug",
        "DatabaseName": "db-tenant-b",
        "Features:BetaDashboard": "true",
        # Overrides the shared value, proving tenant settings win the merge.
        "App:SupportEmail": "vip@contoso.example",
    },
}


def expected_config(tenant_id: str) -> dict[str, str]:
    """The merged result every pattern must produce for this tenant."""
    return {**SHARED_SETTINGS, **TENANT_SETTINGS[tenant_id]}
```

- [ ] **Step 6: サンプルデータのテストを追加**

Append to `tests/test_fake.py`:

```python
def test_tenant_settings_override_shared_settings():
    from mtappconfig import sampledata

    assert sampledata.SHARED_SETTINGS["App:SupportEmail"] == "support@contoso.example"
    assert sampledata.expected_config("tenant-b")["App:SupportEmail"] == "vip@contoso.example"
    assert sampledata.expected_config("tenant-a")["App:SupportEmail"] == "support@contoso.example"
```

- [ ] **Step 7: テストを実行**

Run: `uv run --offline pytest tests/test_fake.py -v`
Expected: PASS（12件）

- [ ] **Step 8: コミット**

```bash
git add src/mtappconfig/fake.py src/mtappconfig/sampledata.py tests/test_fake.py
git commit -m "feat: add in-memory fake store and canonical sample data"
```

---

### Task 5: 構造化ログ

**Files:**
- Create: `src/mtappconfig/observability.py`
- Test: `tests/test_observability.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `JsonFormatter(logging.Formatter)`
  - `configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> None`
  - `get_logger(name: str) -> logging.Logger`

WAF のオペレーショナルエクセレンスの要件「すべてのログに `tenant_id` を付与する」を満たす。`logger.info(..., extra={"tenant_id": ...})` で付けた項目が JSON に出ること。

- [ ] **Step 1: 失敗するテストを書く**

Create: `tests/test_observability.py`

```python
"""Logs must carry the tenant id so one tenant's activity can be isolated."""

import io
import json
import logging

from mtappconfig.observability import JsonFormatter, get_logger


def _capture(record_call):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = get_logger("test.observability")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    record_call(logger)
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_emits_json_with_level_and_message():
    (entry,) = _capture(lambda log: log.info("loaded tenant config"))

    assert entry["level"] == "INFO"
    assert entry["message"] == "loaded tenant config"
    assert "time" in entry


def test_includes_tenant_id_when_supplied():
    (entry,) = _capture(
        lambda log: log.info(
            "loaded tenant config",
            extra={"tenant_id": "tenant-a", "event": "config.load"},
        )
    )

    assert entry["tenant_id"] == "tenant-a"
    assert entry["event"] == "config.load"


def test_omits_tenant_id_when_absent():
    (entry,) = _capture(lambda log: log.info("started"))

    assert "tenant_id" not in entry


def test_includes_the_exception_when_logging_an_error():
    def emit(log):
        try:
            raise ValueError("store unreachable")
        except ValueError:
            log.warning("refresh failed", extra={"tenant_id": "tenant-a"}, exc_info=True)

    (entry,) = _capture(emit)

    assert "ValueError: store unreachable" in entry["error"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest tests/test_observability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtappconfig.observability'`

- [ ] **Step 3: 実装を書く**

Create: `src/mtappconfig/observability.py`

```python
"""Structured logging.

Every log line carries the tenant id when one is in scope, which is what makes
it possible to pull one tenant's activity out of a shared application tier.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import IO

# Fields promoted from `extra=` onto the top level of the log record.
_PROMOTED_FIELDS = ("tenant_id", "event", "pattern")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _PROMOTED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run --offline pytest tests/test_observability.py -v`
Expected: PASS（4件）

- [ ] **Step 5: コミット**

```bash
git add src/mtappconfig/observability.py tests/test_observability.py
git commit -m "feat: add structured logging with tenant id"
```

---

### Task 6: テナント別キャッシュ（TTL + LRU + 劣化運転）

**Files:**
- Create: `src/mtappconfig/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `mtappconfig.source.TenantConfig`, `mtappconfig.source.TenantConfigSource`, `mtappconfig.observability.get_logger`
- Produces:
  - `CacheStats(hits, misses, evictions, expirations, refresh_failures)` — すべて `int`、既定 0
  - `TenantConfigCache(source, *, max_entries=64, ttl_seconds=300.0, refresh_interval_seconds=30.0, clock=time.monotonic, logger=None)` — `.get(tenant_id: str) -> TenantConfig`、`.stats: CacheStats`、`.snapshot() -> dict[str, object]`

設計書 3.4 の中核。記事が .NET の `IMemoryCache` で述べている性質（テナント ID をキーにする / メモリ逼迫時に未使用を退避 / テナントごとの有効期限）を、LRU + TTL で満たす。加えて WAF の信頼性要件として、**リフレッシュが失敗してもキャッシュ済みの値で応答を続ける**。

- [ ] **Step 1: 失敗するテストを書く**

Create: `tests/test_cache.py`

```python
"""Per-tenant caching: the guidance's application-side caching, made observable."""

import pytest

from mtappconfig.cache import TenantConfigCache
from mtappconfig.source import ConfigStoreUnavailableError, TenantConfig


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingSource:
    """Counts loads and reloads so cache behaviour is directly observable."""

    name = "recording"

    def __init__(self, values=None, fail_reload=False):
        self.values = values or {"LogLevel": "Warning"}
        self.loads = []
        self.reloads = 0
        self.fail_reload = fail_reload

    def load(self, tenant_id):
        self.loads.append(tenant_id)

        def reload():
            self.reloads += 1
            if self.fail_reload:
                raise ConfigStoreUnavailableError("store is down")
            return self.values

        return TenantConfig(tenant_id=tenant_id, values=dict(self.values), reload=reload)

    def ping(self):
        return None


@pytest.fixture
def clock():
    return FakeClock()


def test_first_get_is_a_miss_and_loads_from_the_source(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock)

    config = cache.get("tenant-a")

    assert config.values == {"LogLevel": "Warning"}
    assert source.loads == ["tenant-a"]
    assert cache.stats.misses == 1
    assert cache.stats.hits == 0


def test_second_get_is_a_hit_and_does_not_reload_within_the_interval(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, refresh_interval_seconds=30.0)

    cache.get("tenant-a")
    cache.get("tenant-a")

    assert source.loads == ["tenant-a"]
    assert source.reloads == 0, "refresh within the interval must be a no-op"
    assert cache.stats.hits == 1


def test_refresh_happens_once_the_interval_has_passed(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, refresh_interval_seconds=30.0)
    cache.get("tenant-a")

    clock.advance(31)
    cache.get("tenant-a")

    assert source.reloads == 1
    assert source.loads == ["tenant-a"], "a refresh is not a reload from scratch"


def test_ttl_expiry_reloads_from_the_source(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, ttl_seconds=300.0)
    cache.get("tenant-a")

    clock.advance(301)
    cache.get("tenant-a")

    assert source.loads == ["tenant-a", "tenant-a"]
    assert cache.stats.expirations == 1
    assert cache.stats.misses == 2


def test_lru_evicts_the_least_recently_used_tenant(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, max_entries=2)

    cache.get("tenant-a")
    cache.get("tenant-b")
    cache.get("tenant-a")  # tenant-a is now the most recently used
    cache.get("tenant-c")  # evicts tenant-b

    assert cache.stats.evictions == 1
    assert set(cache.snapshot()["entries"]) == {"tenant-a", "tenant-c"}


def test_each_tenant_is_cached_separately(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock)

    cache.get("tenant-a")
    cache.get("tenant-b")

    assert source.loads == ["tenant-a", "tenant-b"]
    assert cache.stats.misses == 2


def test_a_failing_refresh_keeps_serving_the_cached_values(clock):
    """WAF reliability: an unreachable store degrades, it does not fail."""
    source = RecordingSource(fail_reload=True)
    cache = TenantConfigCache(source, clock=clock, refresh_interval_seconds=30.0)
    cache.get("tenant-a")

    clock.advance(31)
    config = cache.get("tenant-a")

    assert config.values == {"LogLevel": "Warning"}
    assert cache.stats.refresh_failures == 1


def test_a_failing_refresh_is_not_retried_until_the_next_interval(clock):
    source = RecordingSource(fail_reload=True)
    cache = TenantConfigCache(source, clock=clock, refresh_interval_seconds=30.0)
    cache.get("tenant-a")
    clock.advance(31)
    cache.get("tenant-a")

    cache.get("tenant-a")

    assert source.reloads == 1


def test_snapshot_reports_configuration_and_stats(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, max_entries=8, ttl_seconds=120.0)
    cache.get("tenant-a")

    snapshot = cache.snapshot()

    assert snapshot["max_entries"] == 8
    assert snapshot["ttl_seconds"] == 120.0
    assert snapshot["entries"] == ["tenant-a"]
    assert snapshot["stats"]["misses"] == 1
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtappconfig.cache'`

- [ ] **Step 3: 実装を書く**

Create: `src/mtappconfig/cache.py`

```python
"""Per-tenant configuration cache.

The guidance is explicit that a multitenant application should load each
tenant's settings on demand rather than loading every tenant's settings at
once, and should cache them keyed by tenant id. This is that cache:

* LRU eviction bounds memory, standing in for the .NET cache's ability to
  drop unused entries under memory pressure.
* A TTL bounds staleness per entry.
* Refresh is activity-driven and rate-limited, because the Python provider
  does not refresh in the background the way the .NET provider does.
* A failed refresh is logged and swallowed: the tenant keeps being served
  from cache rather than seeing an error.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from logging import Logger

from .observability import get_logger
from .source import TenantConfig, TenantConfigSource


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    refresh_failures: int = 0


@dataclass
class _Entry:
    config: TenantConfig
    loaded_at: float
    last_refresh_at: float


class TenantConfigCache:
    def __init__(
        self,
        source: TenantConfigSource,
        *,
        max_entries: int = 64,
        ttl_seconds: float = 300.0,
        refresh_interval_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        logger: Logger | None = None,
    ) -> None:
        self._source = source
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._refresh_interval = refresh_interval_seconds
        self._clock = clock
        self._logger = logger or get_logger(__name__)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self.stats = CacheStats()

    def get(self, tenant_id: str) -> TenantConfig:
        """Return this tenant's config, loading or refreshing as needed.

        The tenant id must already have been validated by the registry.
        """
        now = self._clock()
        entry = self._entries.get(tenant_id)

        if entry is not None and now - entry.loaded_at >= self._ttl:
            del self._entries[tenant_id]
            self.stats.expirations += 1
            entry = None

        if entry is None:
            self.stats.misses += 1
            entry = _Entry(
                config=self._source.load(tenant_id),
                loaded_at=now,
                last_refresh_at=now,
            )
            self._entries[tenant_id] = entry
            self._evict_over_capacity()
            return entry.config

        self.stats.hits += 1
        self._entries.move_to_end(tenant_id)
        if now - entry.last_refresh_at >= self._refresh_interval:
            # Mark the attempt before making it, so a failing store is retried
            # on the next interval rather than on every single request.
            entry.last_refresh_at = now
            try:
                entry.config.refresh()
            except Exception:
                self.stats.refresh_failures += 1
                self._logger.warning(
                    "config refresh failed; serving cached values",
                    extra={"tenant_id": tenant_id, "event": "config.refresh.failed"},
                    exc_info=True,
                )
        return entry.config

    def snapshot(self) -> dict[str, object]:
        """A view of the cache, exposed at /_diagnostics/cache."""
        return {
            "max_entries": self._max_entries,
            "ttl_seconds": self._ttl,
            "refresh_interval_seconds": self._refresh_interval,
            "entries": list(self._entries),
            "stats": asdict(self.stats),
        }

    def _evict_over_capacity(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self.stats.evictions += 1
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run --offline pytest tests/test_cache.py -v`
Expected: PASS（9件）

- [ ] **Step 5: コミット**

```bash
git add src/mtappconfig/cache.py tests/test_cache.py
git commit -m "feat: add per-tenant TTL and LRU config cache"
```

---

### Task 7: Flask アプリケーションファクトリ

**Files:**
- Create: `src/mtappconfig/webapp.py`
- Test: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `TenantRegistry`, `UnknownTenantError`, `TenantConfigSource`, `ConfigStoreUnavailableError`, `TenantConfigCache`
- Produces: `create_app(*, source: TenantConfigSource, registry: TenantRegistry, pattern_name: str, cache: TenantConfigCache | None = None) -> Flask`

3サンプルはこの1つの `create_app` を共有する。設計書 3.6 の6エンドポイントを実装する。

- [ ] **Step 1: 失敗するテストを書く**

Create: `tests/test_webapp.py`

```python
"""The Flask surface shared by all three samples."""

import pytest

from mtappconfig.cache import TenantConfigCache
from mtappconfig.sampledata import TENANTS
from mtappconfig.source import ConfigStoreUnavailableError, TenantConfig
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app


class StubSource:
    name = "stub"

    def __init__(self):
        self.unavailable = False

    def load(self, tenant_id):
        return TenantConfig(
            tenant_id=tenant_id,
            values={"DisplayName": f"Display {tenant_id}", "LogLevel": "Warning"},
        )

    def ping(self):
        if self.unavailable:
            raise ConfigStoreUnavailableError("stub is down")


@pytest.fixture
def source():
    return StubSource()


@pytest.fixture
def client(source):
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name="stub-pattern",
    )
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_lists_tenants_and_names_the_pattern(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "tenant-a" in body
    assert "tenant-b" in body
    assert "stub-pattern" in body


def test_tenant_page_renders_resolved_settings(client):
    response = client.get("/t/tenant-a/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Display tenant-a" in body
    assert "LogLevel" in body


def test_tenant_api_returns_json(client):
    response = client.get("/t/tenant-a/api/config")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tenant_id"] == "tenant-a"
    assert payload["pattern"] == "stub-pattern"
    assert payload["values"]["LogLevel"] == "Warning"


def test_unregistered_tenant_is_not_found(client):
    assert client.get("/t/tenant-zzz/").status_code == 404
    assert client.get("/t/tenant-zzz/api/config").status_code == 404


def test_malformed_tenant_id_is_not_found(client):
    """A hostile id must never reach the store."""
    assert client.get("/t/TENANT-A/").status_code == 404
    assert client.get("/t/ab/").status_code == 404


def test_healthz_does_not_touch_the_store(client, source):
    source.unavailable = True

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_readyz_reports_the_store_state(client, source):
    assert client.get("/readyz").status_code == 200

    source.unavailable = True
    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.get_json()["status"] == "unavailable"


def test_cache_diagnostics_expose_hits_and_misses(client):
    client.get("/t/tenant-a/api/config")
    client.get("/t/tenant-a/api/config")

    payload = client.get("/_diagnostics/cache").get_json()

    assert payload["stats"]["misses"] == 1
    assert payload["stats"]["hits"] == 1
    assert payload["entries"] == ["tenant-a"]


def test_an_injected_cache_is_used(source):
    cache = TenantConfigCache(source, max_entries=1)
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name="stub-pattern",
        cache=cache,
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    client.get("/t/tenant-a/api/config")

    assert cache.stats.misses == 1
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest tests/test_webapp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtappconfig.webapp'`

- [ ] **Step 3: 実装を書く**

Create: `src/mtappconfig/webapp.py`

```python
"""The Flask application every sample shares.

Only the source differs between the samples, so the routes, the tenant
validation, and the caching all live here exactly once.
"""

from __future__ import annotations

from flask import Flask, abort, jsonify, render_template_string

from .cache import TenantConfigCache
from .observability import get_logger
from .source import ConfigStoreUnavailableError, TenantConfigSource
from .tenants import TenantRegistry, UnknownTenantError

_INDEX_TEMPLATE = """
<!doctype html>
<title>Multitenant App Configuration sample</title>
<h1>Multitenant App Configuration sample</h1>
<p>Pattern: <strong>{{ pattern }}</strong></p>
<h2>Tenants</h2>
<ul>
{% for tenant in tenants %}
  <li><a href="/t/{{ tenant.tenant_id }}/">{{ tenant.display_name }}</a>
      <code>{{ tenant.tenant_id }}</code></li>
{% endfor %}
</ul>
<p><a href="/_diagnostics/cache">Cache diagnostics</a></p>
"""

_TENANT_TEMPLATE = """
<!doctype html>
<title>{{ tenant.display_name }}</title>
<h1>{{ tenant.display_name }}</h1>
<p>Pattern: <strong>{{ pattern }}</strong> &middot;
   Tenant: <code>{{ tenant.tenant_id }}</code></p>
<table border="1" cellpadding="6">
  <tr><th>Key</th><th>Value</th></tr>
{% for key, value in values.items() %}
  <tr><td><code>{{ key }}</code></td><td>{{ value }}</td></tr>
{% endfor %}
</table>
<p><a href="/t/{{ tenant.tenant_id }}/api/config">JSON</a> &middot;
   <a href="/">All tenants</a></p>
"""


def create_app(
    *,
    source: TenantConfigSource,
    registry: TenantRegistry,
    pattern_name: str,
    cache: TenantConfigCache | None = None,
) -> Flask:
    app = Flask(__name__)
    config_cache = cache if cache is not None else TenantConfigCache(source)
    logger = get_logger(__name__)

    def _resolve(tenant_id: str):
        """Validate before anything reaches the configuration store."""
        try:
            return registry.resolve(tenant_id)
        except UnknownTenantError:
            logger.warning(
                "rejected tenant id",
                extra={"event": "tenant.rejected", "pattern": pattern_name},
            )
            abort(404)

    @app.get("/")
    def index():
        return render_template_string(
            _INDEX_TEMPLATE, tenants=list(registry), pattern=pattern_name
        )

    @app.get("/t/<tenant_id>/")
    def tenant_page(tenant_id: str):
        tenant = _resolve(tenant_id)
        config = config_cache.get(tenant.tenant_id)
        return render_template_string(
            _TENANT_TEMPLATE,
            tenant=tenant,
            values=dict(config.values),
            pattern=pattern_name,
        )

    @app.get("/t/<tenant_id>/api/config")
    def tenant_config(tenant_id: str):
        tenant = _resolve(tenant_id)
        config = config_cache.get(tenant.tenant_id)
        logger.info(
            "served tenant config",
            extra={
                "tenant_id": tenant.tenant_id,
                "event": "config.served",
                "pattern": pattern_name,
            },
        )
        return jsonify(
            {
                "tenant_id": tenant.tenant_id,
                "display_name": tenant.display_name,
                "pattern": pattern_name,
                "values": dict(config.values),
            }
        )

    @app.get("/healthz")
    def healthz():
        """Liveness. Deliberately does not touch the configuration store."""
        return jsonify({"status": "ok"})

    @app.get("/readyz")
    def readyz():
        try:
            source.ping()
        except ConfigStoreUnavailableError as error:
            return jsonify({"status": "unavailable", "detail": str(error)}), 503
        return jsonify({"status": "ready"})

    @app.get("/_diagnostics/cache")
    def cache_diagnostics():
        return jsonify(config_cache.snapshot())

    return app
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run --offline pytest tests/test_webapp.py -v`
Expected: PASS（9件）

- [ ] **Step 5: 全テストを実行**

Run: `uv run --offline pytest -v`
Expected: 全 PASS

- [ ] **Step 6: コミット**

```bash
git add src/mtappconfig/webapp.py tests/test_webapp.py
git commit -m "feat: add the Flask application factory shared by every sample"
```

---

### Task 8: サンプル01 — 共有ストア + キープレフィックス

**Files:**
- Modify: `conftest.py`（`samples/*/` を `sys.path` に追加）
- Create: `samples/01-shared-store-key-prefix/source_key_prefix.py`
- Create: `samples/01-shared-store-key-prefix/seed_key_prefix.py`
- Create: `samples/01-shared-store-key-prefix/app.py`
- Test: `samples/01-shared-store-key-prefix/tests/test_key_prefix.py`

**Interfaces:**
- Consumes: `FakeAppConfigurationStore`, `TenantConfig`, `sampledata.SHARED_SETTINGS`, `sampledata.TENANT_SETTINGS`, `sampledata.TENANTS`, `create_app`, `TenantRegistry`
- Produces:
  - `source_key_prefix.SHARED_PREFIX: str`（`"shared/"`）
  - `source_key_prefix.KeyPrefixSource(store)` — `TenantConfigSource` の実装
  - `seed_key_prefix.build_store() -> FakeAppConfigurationStore`

**設計書からの逸脱（意図的）:** 設計書 3.1 では各サンプルのモジュール名を `source.py` / `seed.py` としていたが、pytest は3サンプルを1セッションで収集するため、同名モジュールが `sys.modules` で衝突し、2本目以降のサンプルが1本目の実装をテストしてしまう。モジュール名をサンプルごとに一意にして回避する。ファイル構造は3本で揃えるので、`diff samples/01-*/source_key_prefix.py samples/02-*/source_label.py` でパターン差分を読む用途は保たれる。

- [ ] **Step 1: `conftest.py` にサンプルディレクトリを `sys.path` へ追加**

Modify: `conftest.py` — 冒頭の import 群の直後に追加

```python
import sys
from pathlib import Path

# Each sample directory holds its own pattern module. Adding them to sys.path
# lets both the sample's own tests and the cross-pattern contract test import
# them. Module names are unique per sample so nothing collides.
_SAMPLES = Path(__file__).parent / "samples"
if _SAMPLES.is_dir():
    for _sample_dir in sorted(p for p in _SAMPLES.iterdir() if p.is_dir()):
        sys.path.insert(0, str(_sample_dir))
```

- [ ] **Step 2: 失敗するテストを書く**

Create: `samples/01-shared-store-key-prefix/tests/test_key_prefix.py`

```python
"""Shared store, tenant settings behind a `<tenant-id>/` key prefix."""

import pytest

from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app
from seed_key_prefix import build_store
from source_key_prefix import SHARED_PREFIX, KeyPrefixSource


@pytest.fixture
def store():
    return build_store()


@pytest.fixture
def source(store):
    return KeyPrefixSource(store)


def test_seeded_store_uses_tenant_prefixed_keys(store):
    keys = store.select(key_filter="*").keys()

    assert "tenant-a/LogLevel" in keys
    assert f"{SHARED_PREFIX}App:Version" in keys


def test_resolves_the_merged_config_for_each_tenant(source):
    for tenant in TENANTS:
        assert source.load(tenant.tenant_id).values == expected_config(tenant.tenant_id)


def test_the_prefix_is_trimmed_so_the_app_sees_stable_key_names(source):
    values = source.load("tenant-a").values

    assert "LogLevel" in values
    assert not any(key.startswith("tenant-a/") for key in values)


def test_one_tenant_never_sees_another_tenants_values(source):
    values = source.load("tenant-a").values

    assert values["DatabaseName"] == "db-tenant-a"
    assert "db-tenant-b" not in values.values()


def test_tenant_settings_override_shared_settings(source):
    assert source.load("tenant-b").values["App:SupportEmail"] == "vip@contoso.example"
    assert source.load("tenant-a").values["App:SupportEmail"] == "support@contoso.example"


def test_refresh_picks_up_a_changed_value(store, source):
    config = source.load("tenant-a")
    store.set("tenant-a/LogLevel", "Error")

    assert config.refresh() is True
    assert config.values["LogLevel"] == "Error"


def test_ping_propagates_an_unavailable_store(store, source):
    from mtappconfig.source import ConfigStoreUnavailableError

    store.unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        source.ping()


def test_serves_over_http(source):
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name=source.name,
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    payload = client.get("/t/tenant-b/api/config").get_json()

    assert payload["values"] == expected_config("tenant-b")
    assert payload["pattern"] == "shared-store-key-prefix"
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `uv run --offline pytest samples/01-shared-store-key-prefix -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seed_key_prefix'`

- [ ] **Step 4: パターンの実装を書く**

Create: `samples/01-shared-store-key-prefix/source_key_prefix.py`

```python
"""Pattern 1: one shared store, tenants separated by a key prefix.

This is the arrangement the guidance recommends by default. Every tenant's
settings live in the same store under a `<tenant-id>/` prefix, and the prefix
is trimmed on load so the application always sees the same key names.
"""

from __future__ import annotations

from mtappconfig.source import TenantConfig

# Global settings live under their own prefix so they never collide with a
# tenant prefix. Keeping them in one place is the guidance's point about
# shared settings: one value, one place to update.
SHARED_PREFIX = "shared/"


class KeyPrefixSource:
    name = "shared-store-key-prefix"

    def __init__(self, store) -> None:
        self._store = store

    def load(self, tenant_id: str) -> TenantConfig:
        def reload() -> dict[str, str]:
            shared = self._store.select(
                key_filter=f"{SHARED_PREFIX}*",
                trim_prefixes=[SHARED_PREFIX],
            )
            # The tenant id reaching this line has already been validated by
            # TenantRegistry.resolve. An unvalidated id here would let a caller
            # pass "*" and read every tenant's settings at once.
            tenant = self._store.select(
                key_filter=f"{tenant_id}/*",
                trim_prefixes=[f"{tenant_id}/"],
            )
            return {**shared, **tenant}

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)

    def ping(self) -> None:
        self._store.ping()
```

- [ ] **Step 5: シードを書く**

Create: `samples/01-shared-store-key-prefix/seed_key_prefix.py`

```python
"""Lay the canonical sample data out the way this pattern expects it."""

from __future__ import annotations

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS

from source_key_prefix import SHARED_PREFIX


def build_store() -> FakeAppConfigurationStore:
    store = FakeAppConfigurationStore(name="shared-store")
    for key, value in SHARED_SETTINGS.items():
        store.set(f"{SHARED_PREFIX}{key}", value)
    for tenant_id, settings in TENANT_SETTINGS.items():
        for key, value in settings.items():
            store.set(f"{tenant_id}/{key}", value)
    return store
```

- [ ] **Step 6: エントリポイントを書く**

Create: `samples/01-shared-store-key-prefix/app.py`

```python
"""Run with: uv run flask --app app run --port 5001

Set APPCONFIG_ENDPOINT to point at a real store; leave it unset to use the
in-memory fake.
"""

from __future__ import annotations

import os

import pathlib
import sys

# This project is not installed as a package (see pyproject.toml), so put the
# shared core and this sample's own modules on the path explicitly.
_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2] / "src"))
sys.path.insert(0, str(_HERE.parent))

from mtappconfig.observability import configure_logging
from mtappconfig.sampledata import TENANTS
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app

from seed_key_prefix import build_store
from source_key_prefix import KeyPrefixSource


def _build_store():
    endpoint = os.environ.get("APPCONFIG_ENDPOINT")
    if not endpoint:
        return build_store()
    from mtappconfig.azure_source import AzureAppConfigurationStore

    return AzureAppConfigurationStore(endpoint)


configure_logging()
app = create_app(
    source=KeyPrefixSource(_build_store()),
    registry=TenantRegistry(TENANTS),
    pattern_name="shared-store-key-prefix",
)
```

- [ ] **Step 7: テストが通ることを確認**

Run: `uv run --offline pytest samples/01-shared-store-key-prefix -v`
Expected: PASS（8件）

- [ ] **Step 8: 手で動かして確認**

```bash
cd samples/01-shared-store-key-prefix && uv run --offline flask --app app run --port 5001
```

別のシェルで:

```bash
curl -s localhost:5001/t/tenant-a/api/config
curl -s localhost:5001/_diagnostics/cache
```

Expected: 1回目の tenant-a 取得後、`stats.misses` が 1、`entries` が `["tenant-a"]`。もう一度叩くと `hits` が 1 になる。確認できたらサーバを停止する。

- [ ] **Step 9: コミット**

```bash
git add conftest.py samples/01-shared-store-key-prefix
git commit -m "feat: add the shared store with key prefixes sample"
```

---

### Task 9: サンプル02 — 共有ストア + ラベル

**Files:**
- Create: `samples/02-shared-store-label/source_label.py`
- Create: `samples/02-shared-store-label/seed_label.py`
- Create: `samples/02-shared-store-label/app.py`
- Test: `samples/02-shared-store-label/tests/test_label.py`

**Interfaces:**
- Consumes: Task 8 と同じ
- Produces:
  - `source_label.LabelSource(store)` — `TenantConfigSource` の実装
  - `seed_label.build_store() -> FakeAppConfigurationStore`

このサンプルの教材上の要点は、**ラベルがテナント識別に占有されると、バージョニングや環境の区別に使えなくなる**という記事の指摘と、**provider の既定はラベルなしの設定しか読まない**という挙動である。

- [ ] **Step 1: 失敗するテストを書く**

Create: `samples/02-shared-store-label/tests/test_label.py`

```python
"""Shared store, tenant settings separated by label."""

import pytest

from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app
from seed_label import build_store
from source_label import LabelSource


@pytest.fixture
def store():
    return build_store()


@pytest.fixture
def source(store):
    return LabelSource(store)


def test_seeded_store_uses_labels_not_prefixes(store):
    unlabelled = store.select(key_filter="*")
    labelled = store.select(key_filter="*", label_filter="tenant-a")

    assert "App:Version" in unlabelled
    assert "LogLevel" not in unlabelled, "tenant settings must carry a label"
    assert labelled["LogLevel"] == "Warning"


def test_keys_are_not_prefixed_in_this_pattern(store):
    assert not any("/" in key for key in store.select(key_filter="*", label_filter="*"))


def test_resolves_the_merged_config_for_each_tenant(source):
    for tenant in TENANTS:
        assert source.load(tenant.tenant_id).values == expected_config(tenant.tenant_id)


def test_one_tenant_never_sees_another_tenants_values(source):
    values = source.load("tenant-a").values

    assert values["DatabaseName"] == "db-tenant-a"
    assert "db-tenant-b" not in values.values()


def test_tenant_settings_override_shared_settings(source):
    assert source.load("tenant-b").values["App:SupportEmail"] == "vip@contoso.example"
    assert source.load("tenant-a").values["App:SupportEmail"] == "support@contoso.example"


def test_refresh_picks_up_a_changed_value(store, source):
    config = source.load("tenant-a")
    store.set("LogLevel", "Error", label="tenant-a")

    assert config.refresh() is True
    assert config.values["LogLevel"] == "Error"


def test_the_label_is_spent_on_tenancy(store, source):
    """Documents the trade-off: a 'preview' label cannot coexist with the
    tenant label on the same setting, so labels are no longer available for
    versioning or environments."""
    store.set("App:Version", "2.0.0-preview", label="preview")

    assert source.load("tenant-a").values["App:Version"] == "1.4.2"


def test_ping_propagates_an_unavailable_store(store, source):
    from mtappconfig.source import ConfigStoreUnavailableError

    store.unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        source.ping()


def test_serves_over_http(source):
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name=source.name,
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    payload = client.get("/t/tenant-b/api/config").get_json()

    assert payload["values"] == expected_config("tenant-b")
    assert payload["pattern"] == "shared-store-label"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest samples/02-shared-store-label -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seed_label'`

- [ ] **Step 3: パターンの実装を書く**

Create: `samples/02-shared-store-label/source_label.py`

```python
"""Pattern 2: one shared store, tenants separated by label.

The guidance recommends key prefixes over labels for tenancy, because a label
spent on the tenant id is no longer available for versioning or environments.
This pattern is worth choosing mainly when the application is deployed per
tenant, so that a deployment loads exactly one label.
"""

from __future__ import annotations

from mtappconfig.source import TenantConfig


class LabelSource:
    name = "shared-store-label"

    def __init__(self, store) -> None:
        self._store = store

    def load(self, tenant_id: str) -> TenantConfig:
        def reload() -> dict[str, str]:
            # A None label filter selects only unlabelled settings. That is the
            # service's default, and it is why the tenant read below must pass
            # its label explicitly: without it, no tenant setting is returned.
            shared = self._store.select(key_filter="*", label_filter=None)
            # tenant_id has already been validated by TenantRegistry.resolve.
            tenant = self._store.select(key_filter="*", label_filter=tenant_id)
            return {**shared, **tenant}

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)

    def ping(self) -> None:
        self._store.ping()
```

- [ ] **Step 4: シードを書く**

Create: `samples/02-shared-store-label/seed_label.py`

```python
"""Lay the canonical sample data out the way this pattern expects it."""

from __future__ import annotations

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS


def build_store() -> FakeAppConfigurationStore:
    store = FakeAppConfigurationStore(name="shared-store")
    # Shared settings carry no label at all.
    for key, value in SHARED_SETTINGS.items():
        store.set(key, value)
    # Tenant settings use the same key names, told apart only by their label.
    for tenant_id, settings in TENANT_SETTINGS.items():
        for key, value in settings.items():
            store.set(key, value, label=tenant_id)
    return store
```

- [ ] **Step 5: エントリポイントを書く**

Create: `samples/02-shared-store-label/app.py`

```python
"""Run with: uv run flask --app app run --port 5002

Set APPCONFIG_ENDPOINT to point at a real store; leave it unset to use the
in-memory fake.
"""

from __future__ import annotations

import os

import pathlib
import sys

# This project is not installed as a package (see pyproject.toml), so put the
# shared core and this sample's own modules on the path explicitly.
_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2] / "src"))
sys.path.insert(0, str(_HERE.parent))

from mtappconfig.observability import configure_logging
from mtappconfig.sampledata import TENANTS
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app

from seed_label import build_store
from source_label import LabelSource


def _build_store():
    endpoint = os.environ.get("APPCONFIG_ENDPOINT")
    if not endpoint:
        return build_store()
    from mtappconfig.azure_source import AzureAppConfigurationStore

    return AzureAppConfigurationStore(endpoint)


configure_logging()
app = create_app(
    source=LabelSource(_build_store()),
    registry=TenantRegistry(TENANTS),
    pattern_name="shared-store-label",
)
```

- [ ] **Step 6: テストが通ることを確認**

Run: `uv run --offline pytest samples/02-shared-store-label -v`
Expected: PASS（9件）

- [ ] **Step 7: コミット**

```bash
git add samples/02-shared-store-label
git commit -m "feat: add the shared store with labels sample"
```

---

### Task 10: サンプル03 — テナント別ストア

**Files:**
- Create: `samples/03-store-per-tenant/source_store_per_tenant.py`
- Create: `samples/03-store-per-tenant/seed_store_per_tenant.py`
- Create: `samples/03-store-per-tenant/app.py`
- Test: `samples/03-store-per-tenant/tests/test_store_per_tenant.py`

**Interfaces:**
- Consumes: Task 8 と同じ、加えて `mtappconfig.tenants.TenantRegistry`
- Produces:
  - `source_store_per_tenant.StorePerTenantSource(shared_store, tenant_stores: Mapping[str, store])` — `TenantConfigSource` の実装、加えて `.validate_coverage(registry) -> None`
  - `seed_store_per_tenant.build_stores() -> tuple[FakeAppConfigurationStore, dict[str, FakeAppConfigurationStore]]`

グローバル設定は共有ストアに置き、テナント設定はそのテナント専用ストアに置く。記事が述べるとおり、アクセス権限はストア単位でしか制御できないため、権限やCMKを分けたい場合はストアを分けることになる。設計書 3.2 の要件として、**レジストリにいるがストアが未設定のテナントは起動時に検出する**。

- [ ] **Step 1: 失敗するテストを書く**

Create: `samples/03-store-per-tenant/tests/test_store_per_tenant.py`

```python
"""A dedicated store per tenant, plus one shared store for global settings."""

import pytest

from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.source import ConfigStoreUnavailableError
from mtappconfig.tenants import Tenant, TenantRegistry
from mtappconfig.webapp import create_app
from seed_store_per_tenant import build_stores
from source_store_per_tenant import StorePerTenantSource


@pytest.fixture
def stores():
    return build_stores()


@pytest.fixture
def source(stores):
    shared, tenant_stores = stores
    return StorePerTenantSource(shared, tenant_stores)


def test_each_tenant_gets_its_own_store(stores):
    _, tenant_stores = stores

    assert set(tenant_stores) == {"tenant-a", "tenant-b"}
    assert tenant_stores["tenant-a"] is not tenant_stores["tenant-b"]


def test_a_tenant_store_holds_only_that_tenants_settings(stores):
    _, tenant_stores = stores

    values = tenant_stores["tenant-a"].select(key_filter="*")

    assert values["DatabaseName"] == "db-tenant-a"
    assert "db-tenant-b" not in values.values()


def test_resolves_the_merged_config_for_each_tenant(source):
    for tenant in TENANTS:
        assert source.load(tenant.tenant_id).values == expected_config(tenant.tenant_id)


def test_tenant_settings_override_shared_settings(source):
    assert source.load("tenant-b").values["App:SupportEmail"] == "vip@contoso.example"
    assert source.load("tenant-a").values["App:SupportEmail"] == "support@contoso.example"


def test_refresh_picks_up_a_changed_value(stores, source):
    _, tenant_stores = stores
    config = source.load("tenant-a")
    tenant_stores["tenant-a"].set("LogLevel", "Error")

    assert config.refresh() is True
    assert config.values["LogLevel"] == "Error"


def test_a_tenant_without_a_store_is_reported_clearly(stores):
    shared, tenant_stores = stores
    source = StorePerTenantSource(shared, {"tenant-a": tenant_stores["tenant-a"]})

    with pytest.raises(ConfigStoreUnavailableError, match="tenant-b"):
        source.load("tenant-b")


def test_missing_stores_are_detected_at_startup_not_per_request(stores):
    """The spec requires a misconfiguration to surface before serving traffic."""
    shared, tenant_stores = stores
    source = StorePerTenantSource(shared, {"tenant-a": tenant_stores["tenant-a"]})
    registry = TenantRegistry(
        [Tenant("tenant-a", "Tenant A"), Tenant("tenant-b", "Tenant B")]
    )

    with pytest.raises(ValueError, match="tenant-b"):
        source.validate_coverage(registry)


def test_validate_coverage_passes_when_every_tenant_has_a_store(source):
    assert source.validate_coverage(TenantRegistry(TENANTS)) is None


def test_one_unavailable_tenant_store_fails_readiness(stores, source):
    _, tenant_stores = stores
    tenant_stores["tenant-b"].unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        source.ping()


def test_serves_over_http(source):
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name=source.name,
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    payload = client.get("/t/tenant-b/api/config").get_json()

    assert payload["values"] == expected_config("tenant-b")
    assert payload["pattern"] == "store-per-tenant"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest samples/03-store-per-tenant -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seed_store_per_tenant'`

- [ ] **Step 3: パターンの実装を書く**

Create: `samples/03-store-per-tenant/source_store_per_tenant.py`

```python
"""Pattern 3: one App Configuration store per tenant.

Access permissions on App Configuration are granted at the store level, so
separating tenants into separate stores is what makes separate permissions —
and separate customer-managed keys — possible. The guidance names exactly
those two situations as the reasons to choose this pattern.

Global settings still live in one shared store, so that a change to a global
value is made in one place.
"""

from __future__ import annotations

from collections.abc import Mapping

from mtappconfig.source import ConfigStoreUnavailableError, TenantConfig
from mtappconfig.tenants import TenantRegistry


class StorePerTenantSource:
    name = "store-per-tenant"

    def __init__(self, shared_store, tenant_stores: Mapping[str, object]) -> None:
        self._shared_store = shared_store
        self._tenant_stores = dict(tenant_stores)

    def validate_coverage(self, registry: TenantRegistry) -> None:
        """Fail at startup if any registered tenant has no store.

        Without this, a misconfigured deployment looks healthy until the first
        request for the affected tenant arrives.
        """
        missing = [t.tenant_id for t in registry if t.tenant_id not in self._tenant_stores]
        if missing:
            raise ValueError(f"no configuration store for tenants: {', '.join(missing)}")

    def load(self, tenant_id: str) -> TenantConfig:
        store = self._store_for(tenant_id)

        def reload() -> dict[str, str]:
            shared = self._shared_store.select(key_filter="*")
            # No prefix and no label are needed: the store itself is the
            # boundary, which is what makes the isolation strong here.
            tenant = store.select(key_filter="*")
            return {**shared, **tenant}

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)

    def ping(self) -> None:
        self._shared_store.ping()
        for store in self._tenant_stores.values():
            store.ping()

    def _store_for(self, tenant_id: str):
        try:
            return self._tenant_stores[tenant_id]
        except KeyError:
            raise ConfigStoreUnavailableError(
                f"no configuration store for tenant {tenant_id!r}"
            ) from None
```

- [ ] **Step 4: シードを書く**

Create: `samples/03-store-per-tenant/seed_store_per_tenant.py`

```python
"""Lay the canonical sample data out the way this pattern expects it."""

from __future__ import annotations

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS


def build_stores() -> tuple[FakeAppConfigurationStore, dict[str, FakeAppConfigurationStore]]:
    shared = FakeAppConfigurationStore(name="shared-store")
    for key, value in SHARED_SETTINGS.items():
        shared.set(key, value)

    tenant_stores: dict[str, FakeAppConfigurationStore] = {}
    for tenant_id, settings in TENANT_SETTINGS.items():
        store = FakeAppConfigurationStore(name=f"store-{tenant_id}")
        for key, value in settings.items():
            store.set(key, value)
        tenant_stores[tenant_id] = store

    return shared, tenant_stores
```

- [ ] **Step 5: エントリポイントを書く**

Create: `samples/03-store-per-tenant/app.py`

```python
"""Run with: uv run flask --app app run --port 5003

Set APPCONFIG_SHARED_ENDPOINT and APPCONFIG_ENDPOINTS to point at real stores:

    export APPCONFIG_SHARED_ENDPOINT=https://shared.azconfig.io
    export APPCONFIG_ENDPOINTS='{"tenant-a":"https://a.azconfig.io","tenant-b":"https://b.azconfig.io"}'

Leave them unset to use in-memory fakes.
"""

from __future__ import annotations

import json
import os

import pathlib
import sys

# This project is not installed as a package (see pyproject.toml), so put the
# shared core and this sample's own modules on the path explicitly.
_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2] / "src"))
sys.path.insert(0, str(_HERE.parent))

from mtappconfig.observability import configure_logging
from mtappconfig.sampledata import TENANTS
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app

from seed_store_per_tenant import build_stores
from source_store_per_tenant import StorePerTenantSource


def _build_stores():
    endpoints = os.environ.get("APPCONFIG_ENDPOINTS")
    if not endpoints:
        return build_stores()
    from mtappconfig.azure_source import AzureAppConfigurationStore

    shared_endpoint = os.environ["APPCONFIG_SHARED_ENDPOINT"]
    shared = AzureAppConfigurationStore(shared_endpoint)
    tenant_stores = {
        tenant_id: AzureAppConfigurationStore(endpoint)
        for tenant_id, endpoint in json.loads(endpoints).items()
    }
    return shared, tenant_stores


configure_logging()
registry = TenantRegistry(TENANTS)
shared_store, tenant_stores = _build_stores()
source = StorePerTenantSource(shared_store, tenant_stores)
# Surface a misconfigured deployment now, not on the first request.
source.validate_coverage(registry)

app = create_app(
    source=source,
    registry=registry,
    pattern_name="store-per-tenant",
)
```

- [ ] **Step 6: テストが通ることを確認**

Run: `uv run --offline pytest samples/03-store-per-tenant -v`
Expected: PASS（10件）

- [ ] **Step 7: コミット**

```bash
git add samples/03-store-per-tenant
git commit -m "feat: add the store per tenant sample"
```

---

### Task 11: パターン横断の契約テスト

**Files:**
- Create: `tests/test_pattern_contract.py`

**Interfaces:**
- Consumes: 3サンプルの `source_*` と `seed_*` モジュール、`mtappconfig.sampledata`
- Produces: なし（テストのみ）

このテストが、共通コア構成の設計判断そのものを裏付ける。**同じデータを3通りのレイアウトで格納しても、解決結果は同一になる**ことを示し、パターンが差し替え可能であることを保証する。

- [ ] **Step 1: 契約テストを書く**

Create: `tests/test_pattern_contract.py`

```python
"""All three isolation models must resolve to identical results.

The samples store the same settings in three different layouts. If they did
not agree, the comparison the repository is built around would be meaningless,
and the shared core would be hiding a difference rather than isolating one.
"""

import pytest

from mtappconfig.cache import TenantConfigCache
from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app
from seed_key_prefix import build_store as build_key_prefix_store
from seed_label import build_store as build_label_store
from seed_store_per_tenant import build_stores as build_per_tenant_stores
from source_key_prefix import KeyPrefixSource
from source_label import LabelSource
from source_store_per_tenant import StorePerTenantSource


def _make_key_prefix_source():
    return KeyPrefixSource(build_key_prefix_store())


def _make_label_source():
    return LabelSource(build_label_store())


def _make_store_per_tenant_source():
    shared, tenant_stores = build_per_tenant_stores()
    return StorePerTenantSource(shared, tenant_stores)


SOURCE_FACTORIES = [
    _make_key_prefix_source,
    _make_label_source,
    _make_store_per_tenant_source,
]

SOURCE_IDS = ["key-prefix", "label", "store-per-tenant"]


@pytest.fixture(params=SOURCE_FACTORIES, ids=SOURCE_IDS)
def source(request):
    return request.param()


@pytest.mark.parametrize("tenant", TENANTS, ids=lambda t: t.tenant_id)
def test_every_pattern_resolves_the_expected_config(source, tenant):
    assert source.load(tenant.tenant_id).values == expected_config(tenant.tenant_id)


def test_every_pattern_isolates_tenants(source):
    a = source.load("tenant-a").values
    b = source.load("tenant-b").values

    assert a["DatabaseName"] == "db-tenant-a"
    assert b["DatabaseName"] == "db-tenant-b"
    assert "db-tenant-b" not in a.values()
    assert "db-tenant-a" not in b.values()


def test_every_pattern_merges_shared_settings_underneath_tenant_settings(source):
    assert source.load("tenant-a").values["App:SupportEmail"] == "support@contoso.example"
    assert source.load("tenant-b").values["App:SupportEmail"] == "vip@contoso.example"
    assert source.load("tenant-a").values["App:Version"] == "1.4.2"


def test_all_three_patterns_agree_with_each_other():
    resolved = [
        {tenant.tenant_id: factory().load(tenant.tenant_id).values for tenant in TENANTS}
        for factory in SOURCE_FACTORIES
    ]

    first, *rest = resolved
    for other in rest:
        assert other == first


def test_every_pattern_rejects_an_unregistered_tenant_over_http(source):
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name=source.name,
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    assert client.get("/t/tenant-zzz/api/config").status_code == 404
    assert client.get("/t/*/api/config").status_code == 404


def test_every_pattern_works_with_the_shared_cache(source):
    cache = TenantConfigCache(source)

    first = cache.get("tenant-a").values
    second = cache.get("tenant-a").values

    assert first == second == expected_config("tenant-a")
    assert cache.stats.misses == 1
    assert cache.stats.hits == 1
```

- [ ] **Step 2: テストを実行**

Run: `uv run --offline pytest tests/test_pattern_contract.py -v`
Expected: PASS（19件。`source` フィクスチャが3パターンに展開される）

- [ ] **Step 3: 全テストを実行**

Run: `uv run --offline pytest -v`
Expected: 全 PASS

- [ ] **Step 4: コミット**

```bash
git add tests/test_pattern_contract.py
git commit -m "test: assert all three patterns resolve identically"
```

---

### Task 12: 実 Azure App Configuration への束縛

**Files:**
- Create: `src/mtappconfig/azure_source.py`
- Test: `tests/test_azure_source.py`

**Interfaces:**
- Consumes: `mtappconfig.source.ConfigStoreUnavailableError`
- Produces:
  - `AzureSdkNotInstalledError(RuntimeError)`
  - `AzureAppConfigurationStore(endpoint: str, *, refresh_interval_seconds: float = 30.0, startup_timeout_seconds: int = 100)` — `.select(key_filter="*", label_filter=None, trim_prefixes=()) -> dict[str, str]`、`.ping() -> None`、属性 `.name: str`

**設計の要点:** このクラスは `FakeAppConfigurationStore` と**同じ表面**を持つアダプタである。したがって3つのパターン実装（`KeyPrefixSource` / `LabelSource` / `StorePerTenantSource`）は一行も変えずに実 Azure で動く。パターン固有コードがフェイクにも実サービスにも依存しないことが、この設計の狙いどおりに効いていることの確認になる。

**この環境での制約:** `azure-appconfiguration-provider` を取得できないため、**この実装は実行検証できない**。すべての `azure.*` import は関数内に置き、モジュールの import 自体は常に成功させる。

- [ ] **Step 1: 失敗するテストを書く**

Create: `tests/test_azure_source.py`

```python
"""The real-provider binding, isolated so the rest of the suite never needs it."""

import importlib.util

import pytest

from mtappconfig import azure_source
from mtappconfig.azure_source import AzureAppConfigurationStore, AzureSdkNotInstalledError
from mtappconfig.source import ConfigStoreUnavailableError

azure_sdk_installed = importlib.util.find_spec("azure.appconfiguration.provider") is not None


def test_module_imports_without_the_azure_extra():
    """Nothing here may import azure at module scope: the base install has no
    App Configuration SDK, and a module-scope import would break every test."""
    assert azure_source.AzureAppConfigurationStore is not None


def test_constructing_a_store_does_not_need_the_sdk():
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    assert store.name == "https://example.azconfig.io"


@pytest.mark.skipif(azure_sdk_installed, reason="the Azure SDK is installed")
def test_selecting_without_the_sdk_explains_how_to_install_it():
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    with pytest.raises(AzureSdkNotInstalledError, match="requirements-azure.txt"):
        store.select(key_filter="*")


class _FakeProvider(dict):
    """Stands in for AzureAppConfigurationProvider, which is a Mapping."""

    def __init__(self, values=None):
        super().__init__(values or {"LogLevel": "Warning"})
        self.refresh_calls = 0
        self.closed = False

    def refresh(self):
        self.refresh_calls += 1

    def close(self):
        self.closed = True


class _FakeSdk:
    """A stand-in injected at the _import_sdk seam.

    This exercises the adapter's own logic — provider caching, probing and
    error wrapping. It makes no claim about how the real service behaves;
    the real-Azure path stays unverified in this environment.
    """

    def __init__(self, fail_load=False):
        self.load_calls = []
        self.fail_load = fail_load
        self.providers = []

    def load(self, **kwargs):
        self.load_calls.append(kwargs)
        if self.fail_load:
            raise RuntimeError("cannot reach store")
        provider = _FakeProvider()
        self.providers.append(provider)
        return provider

    def selector(self, **kwargs):
        return kwargs

    def credential(self):
        return "credential"

    def install(self, monkeypatch):
        monkeypatch.setattr(
            azure_source,
            "_import_sdk",
            lambda: (self.load, self.selector, self.credential),
        )
        return self


@pytest.fixture
def sdk(monkeypatch):
    return _FakeSdk().install(monkeypatch)


def test_select_loads_once_per_distinct_query(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    store.select(key_filter="tenant-a/*")
    store.select(key_filter="tenant-a/*")

    assert len(sdk.load_calls) == 1
    assert sdk.providers[0].refresh_calls == 1, "the second read refreshes"


def test_select_loads_separately_for_a_different_query(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    store.select(key_filter="tenant-a/*")
    store.select(key_filter="tenant-b/*")

    assert len(sdk.load_calls) == 2


def test_ping_never_reuses_a_cached_provider(sdk):
    """A cached provider's refresh() is a no-op inside the refresh interval and
    does not raise on failure, so probing through the cache would report a dead
    store as healthy forever after one success."""
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="tenant-a/*")

    store.ping()
    store.ping()

    assert len(sdk.load_calls) == 3, "each ping opens its own connection"
    assert sdk.providers[0].refresh_calls == 0, "the probe must not touch the cache"


def test_ping_closes_its_probe(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    store.ping()

    assert sdk.providers[0].closed is True


def test_ping_uses_a_short_timeout_so_readiness_fails_fast(sdk):
    store = AzureAppConfigurationStore(
        "https://example.azconfig.io", startup_timeout_seconds=100, probe_timeout_seconds=5
    )

    store.ping()

    assert sdk.load_calls[0]["startup_timeout"] == 5


def test_ping_reports_an_unreachable_store(monkeypatch):
    _FakeSdk(fail_load=True).install(monkeypatch)
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    with pytest.raises(ConfigStoreUnavailableError, match="unreachable"):
        store.ping()


def test_select_wraps_a_load_failure(monkeypatch):
    _FakeSdk(fail_load=True).install(monkeypatch)
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    with pytest.raises(ConfigStoreUnavailableError, match="could not load"):
        store.select(key_filter="*")


@pytest.mark.live
def test_reads_from_a_real_store():
    """Run with: uv pip install -r requirements-azure.txt && uv run pytest --run-live

    Requires APPCONFIG_ENDPOINT and a signed-in identity holding the
    App Configuration Data Reader role on that store.
    """
    import os

    endpoint = os.environ["APPCONFIG_ENDPOINT"]
    store = AzureAppConfigurationStore(endpoint)

    store.ping()
    assert isinstance(store.select(key_filter="*"), dict)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --offline pytest tests/test_azure_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtappconfig.azure_source'`

- [ ] **Step 3: 実装を書く**

Create: `src/mtappconfig/azure_source.py`

```python
"""Binding to the real Azure App Configuration provider.

This module presents the same surface as `FakeAppConfigurationStore`, so the
three pattern implementations run unchanged against a real store.

Every azure import happens inside a function. The base install deliberately
omits the App Configuration SDK, and a module-scope import would break the
whole application for anyone who has not installed the `azure` extra.
"""

from __future__ import annotations

from collections.abc import Sequence

from .observability import get_logger
from .source import ConfigStoreUnavailableError

_INSTALL_HINT = "install the Azure SDK: uv pip install -r requirements-azure.txt"

# App Configuration represents "no label" with a null character. Passing None
# through to the provider would mean "any label", which is not the same thing.
_NULL_LABEL = "\0"

# A key filter no real setting matches, used only by the reachability probe.
_PROBE_KEY_FILTER = "mtappconfig-probe-matches-nothing"

_logger = get_logger(__name__)


class AzureSdkNotInstalledError(RuntimeError):
    """The azure extra is not installed in this environment."""


def _import_sdk():
    try:
        from azure.appconfiguration.provider import SettingSelector, load
        from azure.identity import DefaultAzureCredential
    except ImportError as error:
        raise AzureSdkNotInstalledError(
            f"Azure App Configuration SDK is not available; {_INSTALL_HINT}"
        ) from error
    return load, SettingSelector, DefaultAzureCredential


class AzureAppConfigurationStore:
    """One real App Configuration store, behind the fake store's interface."""

    def __init__(
        self,
        endpoint: str,
        *,
        refresh_interval_seconds: float = 30.0,
        startup_timeout_seconds: int = 100,
        probe_timeout_seconds: int = 5,
    ) -> None:
        self.name = endpoint
        self._endpoint = endpoint
        self._refresh_interval = refresh_interval_seconds
        self._startup_timeout = startup_timeout_seconds
        # A readiness probe must fail fast rather than hang for the full
        # startup timeout, so it gets its own, much shorter budget.
        self._probe_timeout = probe_timeout_seconds
        # One provider per distinct query. The provider holds the connection
        # and its own refresh bookkeeping, so it is worth keeping around.
        self._providers: dict[tuple, object] = {}

    def ping(self) -> None:
        """Probe the store over a fresh connection.

        This deliberately does NOT reuse the providers cached by select().
        A cached provider's refresh() is a no-op inside the refresh interval
        and does not raise when it fails, so probing through the cache would
        report healthy forever after the first success — the exact opposite of
        what a readiness check is for.
        """
        load, SettingSelector, DefaultAzureCredential = _import_sdk()
        try:
            probe = load(
                endpoint=self._endpoint,
                credential=DefaultAzureCredential(),
                selects=[
                    SettingSelector(
                        key_filter=_PROBE_KEY_FILTER,
                        label_filter=_NULL_LABEL,
                    )
                ],
                startup_timeout=self._probe_timeout,
            )
        except Exception as error:
            raise ConfigStoreUnavailableError(
                f"App Configuration store {self._endpoint!r} is unreachable: {error}"
            ) from error
        probe.close()

    def select(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> dict[str, str]:
        cache_key = (key_filter, label_filter, tuple(trim_prefixes))
        provider = self._providers.get(cache_key)
        if provider is None:
            provider = self._create_provider(key_filter, label_filter, trim_prefixes)
            self._providers[cache_key] = provider
        else:
            # Activity-driven refresh: a no-op until refresh_interval elapses,
            # so calling this on every request costs nothing most of the time.
            provider.refresh()
        return dict(provider)

    def _create_provider(
        self,
        key_filter: str,
        label_filter: str | None,
        trim_prefixes: Sequence[str],
    ):
        load, SettingSelector, DefaultAzureCredential = _import_sdk()
        try:
            return load(
                endpoint=self._endpoint,
                # Entra ID only. No connection strings, no access keys.
                credential=DefaultAzureCredential(),
                selects=[
                    SettingSelector(
                        key_filter=key_filter,
                        label_filter=_NULL_LABEL if label_filter is None else label_filter,
                    )
                ],
                trim_prefixes=list(trim_prefixes),
                refresh_enabled=True,
                refresh_interval=self._refresh_interval,
                # Reliability: retry a slow or briefly unavailable store on
                # startup rather than failing immediately.
                startup_timeout=self._startup_timeout,
                on_refresh_error=self._on_refresh_error,
            )
        except Exception as error:
            raise ConfigStoreUnavailableError(
                f"could not load configuration from {self._endpoint!r}: {error}"
            ) from error

    def _on_refresh_error(self, error: Exception) -> None:
        """Log and swallow: the cache keeps serving the last known values."""
        _logger.warning(
            "app configuration refresh failed",
            extra={"event": "appconfig.refresh.failed"},
            exc_info=error,
        )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run --offline pytest tests/test_azure_source.py -v`
Expected: 10 passed, 1 skipped（`live` マークがスキップされる）

- [ ] **Step 5: 未検証箇所を README 用に控えておく**

この2点はネットワークが復旧した環境で最初に確認すること。ここに記録し、Task 14 のルート README の「既知の制約」節に転記する（すでに転記済みなら内容が一致しているか確認する）。

1. `SettingSelector(label_filter=...)` で「ラベルなし」を表す値。本実装では `"\0"` を使っている。provider には `LabelFilter.NULL` 定数がある可能性が高く、あればそちらを使う。
2. `load(startup_timeout=...)` の引数名。ドキュメントは `startup_timeout` と記載しているが、実物での確認が必要。

- [ ] **Step 6: Azure SDK 抜きで全テストが通ることを確認**

Run: `uv run --offline pytest -v`
Expected: 全 PASS（`live` のみスキップ）

- [ ] **Step 7: コミット**

```bash
git add src/mtappconfig/azure_source.py tests/test_azure_source.py
git commit -m "feat: add the real App Configuration binding behind an optional extra"
```

---

### Task 13: Bicep によるリソース定義

**Files:**
- Create: `infra/modules/monitoring.bicep`
- Create: `infra/modules/appconfig.bicep`
- Create: `infra/modules/rbac.bicep`
- Create: `samples/01-shared-store-key-prefix/main.bicep`
- Create: `samples/02-shared-store-label/main.bicep`
- Create: `samples/03-store-per-tenant/main.bicep`

**Interfaces:**
- Consumes: なし
- Produces: 各サンプルの `main.bicep`。出力は 01/02 が `endpoint string`、03 が `sharedEndpoint string` と `tenantEndpoints array`

WAF のセキュリティ（`disableLocalAuth`、最小権限 RBAC）とオペレーショナルエクセレンス（IaC、Log Analytics への診断送出）をここで担保する。

- [ ] **Step 1: Log Analytics モジュールを作成**

Create: `infra/modules/monitoring.bicep`

```bicep
@description('Name of the Log Analytics workspace.')
param name string

@description('Location for the workspace.')
param location string = resourceGroup().location

@description('Retention in days.')
param retentionInDays int = 30

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
  }
}

output id string = workspace.id
output name string = workspace.name
```

- [ ] **Step 2: App Configuration モジュールを作成**

Create: `infra/modules/appconfig.bicep`

```bicep
@description('Name of the App Configuration store.')
param name string

@description('Location for the store.')
param location string = resourceGroup().location

@description('Pricing tier. Free allows only 3 stores per region per subscription; since sample 03 also deploys a shared store, that caps it at 2 tenants.')
@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Log Analytics workspace to send diagnostics to.')
param logAnalyticsWorkspaceId string

resource store 'Microsoft.AppConfiguration/configurationStores@2024-05-01' = {
  name: name
  location: location
  sku: {
    name: skuName
  }
  properties: {
    // Security: require Entra ID. Access keys and connection strings are off,
    // so a leaked key cannot be used and the app must use a managed identity.
    disableLocalAuth: true
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'send-to-log-analytics'
  scope: store
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'audit'
        enabled: true
      }
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output id string = store.id
output name string = store.name
output endpoint string = store.properties.endpoint
```

- [ ] **Step 3: RBAC モジュールを作成**

Create: `infra/modules/rbac.bicep`

```bicep
@description('Name of the existing App Configuration store to grant access on.')
param configurationStoreName string

@description('Object id of the managed identity that reads configuration.')
param principalId string

@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param principalType string = 'ServicePrincipal'

// App Configuration Data Reader: read-only access to key-values, and nothing
// else. This is the least privilege the application needs.
var appConfigurationDataReaderRoleId = '516239f1-63e1-4d78-a4de-a74fb236a071'

resource store 'Microsoft.AppConfiguration/configurationStores@2024-05-01' existing = {
  name: configurationStoreName
}

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(store.id, principalId, appConfigurationDataReaderRoleId)
  scope: store
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', appConfigurationDataReaderRoleId)
    principalId: principalId
    principalType: principalType
  }
}
```

- [ ] **Step 4: 共有ストア用の `main.bicep` を2サンプルに作成**

Create: `samples/01-shared-store-key-prefix/main.bicep`

```bicep
targetScope = 'resourceGroup'

@description('Suffix that keeps resource names globally unique.')
param nameSuffix string = uniqueString(resourceGroup().id)

param location string = resourceGroup().location

@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Object id of the managed identity that will read configuration.')
param readerPrincipalId string

module monitoring '../../infra/modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    name: 'log-mtappconfig-${nameSuffix}'
    location: location
  }
}

// One shared store holds every tenant's settings. Samples 01 and 02 deploy
// this file byte-for-byte identically: the two patterns differ in how the
// application queries the store, not in what gets deployed.
module sharedStore '../../infra/modules/appconfig.bicep' = {
  name: 'shared-store'
  params: {
    name: 'appcs-shared-${nameSuffix}'
    location: location
    skuName: skuName
    logAnalyticsWorkspaceId: monitoring.outputs.id
  }
}

module sharedStoreRbac '../../infra/modules/rbac.bicep' = {
  name: 'shared-store-rbac'
  params: {
    configurationStoreName: sharedStore.outputs.name
    principalId: readerPrincipalId
  }
}

output endpoint string = sharedStore.outputs.endpoint
```

Create: `samples/02-shared-store-label/main.bicep` — 上と同一の内容。**この2パターンでインフラが完全に一致することが、記事の「どちらも共有ストア方式であり、違うのはアプリ側のクエリの組み立てだけ」という主張の裏付けになる。** サンプル03との差がインフラ側の差そのものになる。

```bash
cp samples/01-shared-store-key-prefix/main.bicep samples/02-shared-store-label/main.bicep
```

- [ ] **Step 5: テナント別ストア用の `main.bicep` を作成**

Create: `samples/03-store-per-tenant/main.bicep`

```bicep
targetScope = 'resourceGroup'

@description('Suffix that keeps resource names globally unique.')
param nameSuffix string = uniqueString(resourceGroup().id)

param location string = resourceGroup().location

@description('Tenants to provision a dedicated store for. On the free tier only three stores per region per subscription are allowed.')
param tenantIds array = [
  'tenant-a'
  'tenant-b'
]

@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Object id of the managed identity that will read configuration.')
param readerPrincipalId string

module monitoring '../../infra/modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    name: 'log-mtappconfig-${nameSuffix}'
    location: location
  }
}

// Global settings still live in one shared store, so a global change is made
// in one place rather than once per tenant.
module sharedStore '../../infra/modules/appconfig.bicep' = {
  name: 'shared-store'
  params: {
    name: 'appcs-shared-${nameSuffix}'
    location: location
    skuName: skuName
    logAnalyticsWorkspaceId: monitoring.outputs.id
  }
}

module sharedStoreRbac '../../infra/modules/rbac.bicep' = {
  name: 'shared-store-rbac'
  params: {
    configurationStoreName: sharedStore.outputs.name
    principalId: readerPrincipalId
  }
}

// One store per tenant. Permissions on App Configuration are granted at the
// store level, so this is what makes per-tenant permissions possible at all.
module tenantStores '../../infra/modules/appconfig.bicep' = [
  for tenantId in tenantIds: {
    name: 'store-${tenantId}'
    params: {
      name: 'appcs-${tenantId}-${nameSuffix}'
      location: location
      skuName: skuName
      logAnalyticsWorkspaceId: monitoring.outputs.id
    }
  }
]

module tenantStoreRbac '../../infra/modules/rbac.bicep' = [
  for (tenantId, i) in tenantIds: {
    name: 'store-rbac-${tenantId}'
    params: {
      configurationStoreName: tenantStores[i].outputs.name
      principalId: readerPrincipalId
    }
  }
]

output sharedEndpoint string = sharedStore.outputs.endpoint
output tenantEndpoints array = [
  for (tenantId, i) in tenantIds: {
    tenantId: tenantId
    endpoint: tenantStores[i].outputs.endpoint
  }
]
```

- [ ] **Step 6: Bicep をビルドして検証**

```bash
for f in samples/*/main.bicep; do echo "== $f"; az bicep build --file "$f" --stdout > /dev/null && echo OK; done
```

Expected: 3ファイルとも OK。`az` が無い場合は `bicep build --file <path> --stdout` を使う。どちらも無い場合はこのステップをスキップし、Task 14 のルート README の「既知の制約」に「Bicep 未検証」と記載する。

ビルドが通ったら生成物を残さない:

```bash
rm -f samples/*/main.json infra/modules/*.json
```

- [ ] **Step 7: コミット**

```bash
git add infra samples/*/main.bicep
git commit -m "feat: add Bicep for App Configuration stores, RBAC and diagnostics"
```

---

### Task 14: README（ルート + 各サンプル）

**Files:**
- Modify: `README.md`
- Create: `samples/01-shared-store-key-prefix/README.md`
- Create: `samples/02-shared-store-label/README.md`
- Create: `samples/03-store-per-tenant/README.md`

**Interfaces:**
- Consumes: 実装済みの全タスク
- Produces: なし（ドキュメントのみ）

- [ ] **Step 1: ルート README を書く**

Modify: `README.md`（既存の内容を全置換）

````markdown
# マルチテナント × Azure App Configuration サンプル

[Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration)
が示す分離モデルを、動かして違いが分かる Flask サンプル3本にしたものです。
評価軸は [Azure Well-Architected Framework](https://learn.microsoft.com/en-us/azure/well-architected/)。

## 3つのパターン

| | [01 キープレフィックス](samples/01-shared-store-key-prefix/) | [02 ラベル](samples/02-shared-store-label/) | [03 テナント別ストア](samples/03-store-per-tenant/) |
| --- | --- | --- | --- |
| ストア数 | 共有1つ | 共有1つ | テナント数だけ + 共有1つ |
| 分け方 | キー `tenant-a/LogLevel` | ラベル `tenant-a` | ストアそのもの |
| データ分離 | 低 | 低 | 高 |
| 性能分離 | 低 | 低 | 高 |
| デプロイ/運用の複雑さ | 低 | 低 | 中〜高 |
| コスト | 低 | 低 | 中〜高 |
| ラベルの空き | 環境・バージョンに使える | テナントで占有 | 環境・バージョンに使える |

**迷ったら 01。** 記事も既定としてキープレフィックスを推奨しています。ラベルをテナント識別に
使うと、バージョニングや環境の区別にラベルを使えなくなるためです。テナントごとに顧客管理キー
(CMK) が必要、またはテナントが設定データの分離を要求する場合にだけ 03 を選びます。

## 動かす

Azure のサブスクリプションは不要です。既定ではメモリ上のフェイクストアが使われます。

```bash
uv sync
uv run pytest                                   # 全テスト
cd samples/01-shared-store-key-prefix
uv run flask --app app run --port 5001
```

- `http://localhost:5001/` — テナント一覧
- `http://localhost:5001/t/tenant-a/` — 解決後の設定
- `http://localhost:5001/_diagnostics/cache` — キャッシュの hit/miss/evict

実際の App Configuration に繋ぐ場合は `main.bicep` でストアを作り、環境変数を設定します。

```bash
uv pip install -r requirements-azure.txt
export APPCONFIG_ENDPOINT=https://<your-store>.azconfig.io
uv run flask --app app run --port 5001
```

Azure SDK を `pyproject.toml` ではなく `requirements-azure.txt` に置いているのは意図的です。
`uv lock` は optional-dependencies も解決対象に含めるため、そこに書くと「フェイクだけで
動かしたい人」の `uv sync` まで巻き込んで失敗します。

認証は `DefaultAzureCredential` です。実行する ID に **App Configuration Data Reader**
ロールを付与してください（`main.bicep` の `readerPrincipalId` で割り当てられます）。

## 構成

パターン間の差分は各サンプルの `source_*.py` に集約してあります。共通部分
（テナント ID 検証・キャッシュ・Flask のルート）は `src/mtappconfig/` に1回だけ書かれています。

```bash
diff samples/01-shared-store-key-prefix/source_key_prefix.py \
     samples/02-shared-store-label/source_label.py
```

`tests/test_pattern_contract.py` が、**3パターンとも同じ解決結果を返す**ことを検証しています。
だからこの diff がパターンの違いのすべてです。

## Well-Architected の観点

### セキュリティ

- 接続文字列・アクセスキーを使いません。`DefaultAzureCredential` のみで、Bicep は
  `disableLocalAuth: true` を設定します。
- 権限は **App Configuration Data Reader**（`516239f1-63e1-4d78-a4de-a74fb236a071`）だけ。
- **テナント ID の検証がこのサンプルで最も重要な部分です。** URL 由来のテナント ID を検証せずに
  キーフィルタへ渡すと、`*` を指定するだけで全テナントの設定が読めてしまいます。
  `src/mtappconfig/tenants.py` のレジストリ照合と正規表現を通った ID だけがストアに到達します。
- 機密値は App Configuration ではなく Key Vault に置き、Key Vault 参照として保存してください。

### 信頼性

- 設定ストアが落ちてもキャッシュ済みの値で応答を続けます（`src/mtappconfig/cache.py`）。
- `/healthz` は依存先を叩かず、`/readyz` はストア到達性を含みます。

### パフォーマンス効率

- テナント単位の遅延ロード。全テナント一括ロードはしません。
- TTL + LRU でメモリを有界に保ちます。
- リクエスト契機のリフレッシュで、毎リクエストではストアを叩きません。

### コスト最適化

| | Free | Developer | Standard | Premium |
| --- | --- | --- | --- | --- |
| ストア数 | 3 / リージョン / サブスクリプション | 無制限 | 無制限 | 無制限 |
| リクエスト | 1,000 / 日 | 6,000 / 時 | 30,000 / 時 | 制限なし |
| ストレージ | 10 MB | 500 MB | 1 GB | 4 GB |

出典: [Azure subscription and service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-app-configuration)

**Free tier ではストアが1リージョン・1サブスクリプションあたり3つまで**です。パターン03 は
共有ストアも1つ使うので、Free では2テナントまでしか作れません。共有ストア方式でも、
テナント数が増えれば1ストアのリクエスト/時とストレージの上限に達しうるため、その場合は
複数の共有ストアにテナントを分散します。

## 既知の制約

- このリポジトリは PyPI のファイル配信ホスト (`files.pythonhosted.org`) に到達できない
  環境で開発されました。同様の環境では uv コマンドに `--offline` を付けてください
  （`uv sync --offline`、`uv run --offline pytest`）。通常のネットワーク環境では不要です。
- `src/mtappconfig/azure_source.py`（実 App Configuration への接続）は、開発環境から
  `azure-appconfiguration-provider` を取得できないため**実行検証されていません**。
  この1ファイルだけが未検証で、それ以外はフェイクストアに対して全テストが通ります。
  検証時は次の2点を最初に確認してください。
  - `SettingSelector(label_filter=...)` で「ラベルなし」を表す値（本実装は `"\0"`）
  - `load(startup_timeout=...)` の引数名
````

- [ ] **Step 2: サンプル01 の README を書く**

Create: `samples/01-shared-store-key-prefix/README.md`

````markdown
# 01 — 共有ストア + キープレフィックス

1つの App Configuration ストアを全テナントで共有し、キーの先頭にテナント ID を付けて
区別します。**記事が既定として推奨するパターン**です。

## ストアの中身

| キー | 値 | ラベル |
| --- | --- | --- |
| `_shared/App:SupportEmail` | `support@contoso.example` | なし |
| `_shared/App:Version` | `1.4.2` | なし |
| `tenant-a/LogLevel` | `Warning` | なし |
| `tenant-a/DatabaseName` | `db-tenant-a` | なし |
| `tenant-b/LogLevel` | `Debug` | なし |

## 中核のコード

```python
shared = store.select(key_filter="shared/*", trim_prefixes=["shared/"])
tenant = store.select(key_filter=f"{tenant_id}/*", trim_prefixes=[f"{tenant_id}/"])
return {**shared, **tenant}
```

`trim_prefixes` でプレフィックスを削るので、**アプリからは常に `LogLevel` という同じキー名に
見えます**。テナントごとにコードを分ける必要がありません。

`tenant_id` はここに来る前に `TenantRegistry.resolve` を通っています。未検証の ID をここに
渡すと、`*` を指定するだけで全テナントの設定が読めてしまいます。

## いつ選ぶか

- 共有アプリケーション層で多数のテナントを捌く、一般的なマルチテナント構成
- ラベルをバージョニングや環境（dev/prod）の区別に使いたい場合

## 動かす

```bash
uv run flask --app app run --port 5001
curl -s localhost:5001/t/tenant-a/api/config
```

## Azure にデプロイ

```bash
az deployment group create -g <rg> -f main.bicep -p readerPrincipalId=<managed-identity-object-id>
```
````

- [ ] **Step 3: サンプル02 の README を書く**

Create: `samples/02-shared-store-label/README.md`

````markdown
# 02 — 共有ストア + ラベル

1つのストアを共有し、**ラベル**でテナントを区別します。キーにはプレフィックスを付けません。

## ストアの中身

| キー | 値 | ラベル |
| --- | --- | --- |
| `App:SupportEmail` | `support@contoso.example` | なし |
| `App:Version` | `1.4.2` | なし |
| `LogLevel` | `Warning` | `tenant-a` |
| `DatabaseName` | `db-tenant-a` | `tenant-a` |
| `LogLevel` | `Debug` | `tenant-b` |

## 中核のコード

```python
shared = store.select(key_filter="*", label_filter=None)      # ラベルなしのみ
tenant = store.select(key_filter="*", label_filter=tenant_id)
return {**shared, **tenant}
```

**ラベルフィルタを省略すると、ラベルなしの設定しか返りません。** これは App Configuration の
既定の挙動で、テナント設定を読むにはラベル指定が必須です。

## トレードオフ

記事は**テナント識別にはキープレフィックス（パターン01）を推奨**しています。ラベルをテナントに
使ってしまうと、ラベル本来の用途であるバージョニングや環境の区別に使えなくなるためです。
`tests/test_label.py::test_the_label_is_spent_on_tenancy` がこの制約を実際に示しています。

## いつ選ぶか

- **テナントごとにアプリケーションをデプロイしている場合。** 各デプロイが自分のラベルだけを
  読めばよく、ラベルフィルタが自然に効きます。
- それ以外では 01 を選んでください。

## 動かす

```bash
uv run flask --app app run --port 5002
curl -s localhost:5002/t/tenant-a/api/config
```

## Azure にデプロイ

インフラはパターン01 と完全に同一です（共有ストア1つ）。違うのはアプリ側のクエリの
組み立て方だけ、というのがこの2パターンの関係です。

```bash
az deployment group create -g <rg> -f main.bicep -p readerPrincipalId=<managed-identity-object-id>
```
````

- [ ] **Step 4: サンプル03 の README を書く**

Create: `samples/03-store-per-tenant/README.md`

````markdown
# 03 — テナント別ストア

テナントごとに専用の App Configuration ストアを作ります。グローバル設定は共有ストア1つに
置いたままにして、変更箇所を1つに保ちます。

## ストア構成

| ストア | 中身 |
| --- | --- |
| `appcs-shared-*` | `App:SupportEmail`, `App:Version` |
| `appcs-tenant-a-*` | `LogLevel`, `DatabaseName`, ... （tenant-a のみ） |
| `appcs-tenant-b-*` | `LogLevel`, `DatabaseName`, ... （tenant-b のみ） |

## 中核のコード

```python
store = self._store_for(tenant_id)          # テナント → ストアの引き当て
shared = self._shared_store.select(key_filter="*")
tenant = store.select(key_filter="*")
return {**shared, **tenant}
```

プレフィックスもラベルも要りません。**ストアそのものが境界**だからです。これがこのパターンの
データ分離が「高」である理由です。

起動時に `validate_coverage(registry)` を呼び、ストアが未設定のテナントがあればその場で
失敗させます。設定漏れが最初のリクエストまで露見しない状態を作らないためです。

## いつ選ぶか

記事が挙げる採用理由は2つです。

- **テナントごとに異なる顧客管理キー (CMK) が必要な場合**
- **テナントが設定データの分離を要求する場合。** App Configuration のアクセス権限はストア単位
  でしか制御できないため、権限を分けるにはストアを分けるしかありません。

裏を返すと、共有アプリケーション層で全テナントを捌く構成なら、テナント別ストアの利点は
ほとんどありません。

## コスト上の注意

**Free tier ではストアが1リージョン・1サブスクリプションあたり3つまで**です。このパターンは
共有ストアも1つ使うので、Free では2テナントまでしか作れません。Standard 以上ならストア数は
無制限です。ストアが増えるぶんデプロイと運用の対象も増えます。

なお `main.bicep` はこの上限を検査しません。Free tier で3テナント以上を指定すると、
分かりやすいエラーではなく Azure のクォータエラーでデプロイが失敗します。

## 動かす

```bash
uv run flask --app app run --port 5003
curl -s localhost:5003/t/tenant-a/api/config
```

## Azure にデプロイ

```bash
az deployment group create -g <rg> -f main.bicep \
  -p readerPrincipalId=<managed-identity-object-id> \
  -p tenantIds='["tenant-a","tenant-b"]'
```
````

- [ ] **Step 5: README のリンクと手順が正しいか確認**

```bash
uv run --offline pytest -q
ls samples/01-shared-store-key-prefix/source_key_prefix.py \
   samples/02-shared-store-label/source_label.py \
   samples/03-store-per-tenant/source_store_per_tenant.py
diff samples/01-shared-store-key-prefix/main.bicep samples/02-shared-store-label/main.bicep && echo "01 と 02 のインフラは同一"
```

Expected: 全テスト PASS。3つの `source_*.py` が存在。01 と 02 の `main.bicep` が同一。

- [ ] **Step 6: コミット**

```bash
git add README.md samples/*/README.md
git commit -m "docs: document the three patterns and their trade-offs"
```

---

## Self-Review の結果

計画を書き終えたあと、spec に照らして確認した内容と、その場で修正した点。

**1. spec カバレッジ**

| spec の項目 | 対応タスク |
| --- | --- |
| 2. 依存構成（`azure` エクストラ分離） | Task 1 |
| 3.1 ディレクトリ構成 | Task 1, 8-10, 13 |
| 3.2 中心の抽象 | Task 3 |
| 3.2 `APPCONFIG_ENDPOINT(S)` による切替、起動時の未設定検出 | Task 8-10（03 の `validate_coverage`） |
| 3.3 データフロー（検証 → キャッシュ → マージ） | Task 2, 6, 7 |
| 3.4 アプリ側キャッシュ（LRU / TTL / 遅延 / activity-driven） | Task 6 |
| 3.5 共有設定とテナント設定 | Task 4（`sampledata`） |
| 3.6 6つのエンドポイント | Task 7 |
| 4. 各サンプルの実装差分 | Task 8, 9, 10 |
| 5. セキュリティ（Entra ID / RBAC / テナント ID 検証 / Key Vault） | Task 2, 12, 13, 14 |
| 5. 信頼性（リトライ / 劣化運転 / health 分離） | Task 6, 7, 12 |
| 5. パフォーマンス効率 | Task 6 |
| 5. オペレーショナルエクセレンス（IaC / 構造化ログ / 診断） | Task 5, 13 |
| 5. コスト最適化（tier 表と帰結） | Task 14 |
| 6. テスト方針（契約 / セキュリティ / キャッシュ / マージ / HTTP / live） | Task 2, 6, 7, 11, 12 |
| 7. 実装順序 | Task 1-14 の並び |

**修正した点:**

- spec 5「シークレットは Key Vault 参照」に対応するタスクが無かった。フェイクでの
  `secret_resolver` 相当の実装は、この環境で実物の挙動を確認できず、模倣が正しい保証を
  得られないため**実装しない**。ルート README のセキュリティ節に運用上の指針として
  記載するに留める（Task 14 Step 1 に反映済み）。これは spec からの意図的な縮小。
- spec 3.1 のモジュール名 `source.py` / `seed.py` は、pytest が3サンプルを1セッションで
  収集する際に `sys.modules` で衝突する。サンプルごとに一意な名前へ変更した
  （Task 8 に逸脱として明記）。

**2. プレースホルダ走査:** "TBD" / "後で実装" / "適切なエラー処理を追加" の類は無し。
すべてのコードステップに実際のコードがある。

**3. 型と名前の整合:** タスクをまたいで使う名前を突き合わせ済み。

- `TenantConfig(tenant_id, values, reload)` / `.refresh() -> bool` — Task 3 で定義、Task 6・8-10 で使用
- `store.select(key_filter, label_filter, trim_prefixes)` — Task 4 で定義、Task 8-10・12 で使用。
  `AzureAppConfigurationStore`（Task 12）が同じ引数名・同じ戻り値型を持つことを確認済み
- `store.ping()` / `ConfigStoreUnavailableError` — Task 3・4 で定義、Task 7・10・12 で使用
- `TenantConfigCache(...).stats` / `.snapshot()` — Task 6 で定義、Task 7 の `/_diagnostics/cache` と
  Task 11 で使用。`snapshot()` のキー（`max_entries` / `ttl_seconds` / `entries` / `stats`）が
  Task 6 と Task 7 のテストで一致
- `create_app(*, source, registry, pattern_name, cache=None)` — Task 7 で定義、Task 8-11 で使用
- `sampledata.expected_config(tenant_id)` — Task 4 で定義、Task 8-11 で使用
