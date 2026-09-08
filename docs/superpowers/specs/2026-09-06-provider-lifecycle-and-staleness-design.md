# provider のライフサイクルと古さの上限 設計書(R1・R2対応)

> **Topic 移動メモ(2026-09-08):** provider ライフサイクルの実装は、予定していた
> Topic 17 から Topic 11 / PR #15 へ移した。この文書は元の設計経緯を残すが、
> `max_staleness_seconds` 案は修正コミット `3585676` で撤回されており実装しない。
> 最終仕様は `TenantConfig.close` を使い、キャッシュの TTL 切れ/LRU eviction 時に
> 当該テナントの正確な provider だけを close・削除し、次回 load で新しい provider を
> 作る。サンプル04とルート README のレビュー表は Topic 11 の対象外とする。

- 作成日: 2026-09-06
- 目的: 第三者レビュー([`docs/reviews/2026-09-06-independent-review.md`](../../reviews/2026-09-06-independent-review.md))の
  **R1**(LRU の上限が SDK provider に適用されない)と **R2**(TTL と障害時の保証がフェイクと
  実接続で異なる)を解消する。両方とも根本原因は同じ ── `AzureAppConfigurationStore` が
  一度作った SDK provider を無期限に保持し、外側の `TenantConfigCache` が何をしても
  それと連動しないこと。
- 対象: `src/mtappconfig/source.py`、`src/mtappconfig/cache.py`、`src/mtappconfig/fake.py`、
  `src/mtappconfig/azure_source.py`、`samples/{01,02,03,04}-*/source_*.py`
- 参照: [Python provider の refresh 仕様](https://learn.microsoft.com/en-us/azure/azure-app-configuration/reference-python-provider#configuration-refresh)

## 1. 背景

`AzureAppConfigurationStore.select()` は `(key_filter, label_filter, trim_prefixes)` を
キーに SDK provider を `_providers` 辞書へキャッシュし、二度目以降は `provider.refresh()`
(アクティビティ駆動、間隔未経過ならno-op)を呼ぶだけで、**この provider を削除・close する
経路がどこにもない**。

これが2つの独立した問題を生む。

- **R1**: 外側の `TenantConfigCache`(`max_entries` で LRU evict)がテナントを追い出しても、
  対応する SDK provider は `_providers` に残り続ける。テナント数が増えるサービスでは、
  「LRU でメモリを有界に保つ」という README の主張が実 Azure 接続全体には成立しない。
- **R2**: 外側キャッシュが TTL 切れでエントリを消し、`source.load()` を呼び直しても、
  `select()` は同じ cache_key の provider が既にあれば単に `refresh()` するだけで、
  新しい接続は作らない。実 SDK は refresh 失敗を `on_refresh_error` で握りつぶして
  最後に成功した値を返し続けるため、**ストアが恒久的に落ちても `select()` は例外を
  投げず、README が主張する「TTL 切れ後は 503」が成立しない。**

## 2. 方針

### R1 — provider の破棄をテナントの evict/expire に連動させる

`TenantConfig`(`source.py`)に `close` フィールドを追加する。`TenantConfigCache`
(`cache.py`)は、エントリを LRU で evict するとき、および TTL 切れで削除するときに
`entry.config.close()` を呼ぶ(設定されていれば)。各 `source_*.py` は、**そのテナント
自身の provider だけ**を閉じる `close` クロージャを配線する ── 共有プレフィックス/
共有ラベルの provider は他の全テナントが使うため、1テナントの evict で道連れにしては
いけない。

`AzureAppConfigurationStore` に `close(key_filter, label_filter, trim_prefixes)` を
追加し、該当 cache_key の provider を `_providers` から取り出して `.close()` する。
`FakeAppConfigurationStore` にも対応する no-op の `close(...)` を追加する ──
`azure_source.py` の docstring が明言する「フェイクストアと同じ面(surface)を提供する」を
逆方向にも保つため。これにより各 `source_*.py` の `close` クロージャは、ストアの種類を
気にせず同じ呼び出し方で書ける。

サンプル03(`StorePerTenantSource`)だけは形が違う ── テナントは共有ストアではなく
**自分専用の `AzureAppConfigurationStore` インスタンス**を持つ。したがってこのテナントを
evict するとき、テナント専用ストアの `close(key_filter="*")` を呼ぶ(共有ストア側は
一切触らない)。

### R2 — 「最後に成功した時刻」からの経過時間を古さの上限として扱う

`AzureAppConfigurationStore` に `max_staleness_seconds`(既定値あり、後述)を追加する。
provider ごとに「最後に成功した refresh (または作成)時刻」を `_last_success_at` に記録し、
`select()` の中でこの経過時間が `max_staleness_seconds` を超えていれば、provider が生きて
いて古い値を返せる状態であっても `ConfigStoreUnavailableError` を投げる。

「このリフレッシュ呼び出しが成功したか失敗したか」を知るため、`on_refresh_error` を
provider ごと(cache_key ごと)のクロージャにする(現状は `self._on_refresh_error` を
全 provider で使い回している)。`select()` は provider ごとの「このリフレッシュ呼び出し中に
エラーコールバックが発火したか」を表す一時フラグを立てて `provider.refresh()` を呼び、
発火していなければ `_last_success_at` を更新する。

既定値は `refresh_interval_seconds` の3倍(既定 `refresh_interval_seconds=30.0` なら
`max_staleness_seconds=90.0`)。これは「1〜2回の一時的な refresh 失敗は許容し、
3回連続で失敗した相当の期間が経過したら利用不可とみなす」という意図の値であり、
README にその理由を明記する。**既定で有効にする**(呼び出し側が明示的に無効化しない
限り、この保証が実際のデフォルト挙動になる)。

## 3. コード変更

### 3.1 `src/mtappconfig/source.py`

```python
@dataclass
class TenantConfig:
    tenant_id: str
    values: Mapping[str, str]
    reload: Callable[[], Mapping[str, str]] | None = field(default=None, repr=False)
    close: Callable[[], None] | None = field(default=None, repr=False)  # 追加

    def refresh(self) -> bool:
        ...  # 変更なし
```

`close` のデフォルトは `None`。既存3サンプル+04のテスト・呼び出し側は一切変更不要
(位置引数ではなくキーワード引数で渡すため、既存の `TenantConfig(tenant_id=..., values=..., reload=...)` はそのまま動く)。

### 3.2 `src/mtappconfig/cache.py`

`_Entry` に変更は不要(`config: TenantConfig` が既に `close` を持つ)。

`_evict_over_capacity()`:

```python
def _evict_over_capacity(self) -> None:
    while len(self._entries) > self._max_entries:
        _, entry = self._entries.popitem(last=False)
        self._safe_close(entry)
        self.stats.evictions += 1
```

TTL 切れの削除箇所(`get()` 内、現状 `del self._entries[tenant_id]`)も同様に:

```python
if entry is not None and now - entry.loaded_at >= self._ttl:
    self._safe_close(entry)
    del self._entries[tenant_id]
    self.stats.expirations += 1
    entry = None
```

共通ヘルパー:

```python
def _safe_close(self, entry: _Entry) -> None:
    if entry.config.close is None:
        return
    try:
        entry.config.close()
    except Exception:
        self._logger.warning(
            "closing evicted tenant's config source failed",
            extra={"tenant_id": entry.config.tenant_id, "event": "config.close.failed"},
            exc_info=True,
        )
```

close の失敗はログして握りつぶす(evict/expire 自体を失敗させてはいけない ── 呼び出し元は
新しいエントリをロードする途中であり、古い provider の破棄失敗でその処理を止める理由はない)。

### 3.3 `src/mtappconfig/fake.py`

```python
class FakeAppConfigurationStore:
    ...
    def close(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> None:
        """No-op: the fake holds nothing that needs disposing.

        Exists so every source_*.py can call store.close(...) uniformly,
        without checking whether the store is real or fake.
        """
```

引数は受け取るが完全に無視する(何もしない)。既存のテスト・サンプルの挙動には一切影響しない。

### 3.4 `src/mtappconfig/azure_source.py`

```python
from __future__ import annotations

import time
from collections.abc import Sequence

...

class AzureAppConfigurationStore:
    def __init__(
        self,
        endpoint: str,
        *,
        refresh_interval_seconds: float = 30.0,
        startup_timeout_seconds: int = 100,
        probe_timeout_seconds: int = 5,
        max_staleness_seconds: float | None = None,
        clock=time.monotonic,
    ) -> None:
        self.name = endpoint
        self._endpoint = endpoint
        self._refresh_interval = refresh_interval_seconds
        self._startup_timeout = startup_timeout_seconds
        self._probe_timeout = probe_timeout_seconds
        self._max_staleness = (
            refresh_interval_seconds * 3.0 if max_staleness_seconds is None else max_staleness_seconds
        )
        self._clock = clock
        self._providers: dict[tuple, object] = {}
        self._last_success_at: dict[tuple, float] = {}
        self._pending_failure: dict[tuple, bool] = {}

    def close(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> None:
        """Drop and close the provider for this exact query, if one exists."""
        cache_key = (key_filter, label_filter, tuple(trim_prefixes))
        provider = self._providers.pop(cache_key, None)
        self._last_success_at.pop(cache_key, None)
        if provider is not None:
            provider.close()

    def select(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> dict[str, str]:
        cache_key = (key_filter, label_filter, tuple(trim_prefixes))
        provider = self._providers.get(cache_key)
        if provider is None:
            provider = self._create_provider(key_filter, label_filter, trim_prefixes, cache_key)
            self._providers[cache_key] = provider
            self._last_success_at[cache_key] = self._clock()
        else:
            self._pending_failure[cache_key] = False
            provider.refresh()
            if not self._pending_failure.pop(cache_key):
                self._last_success_at[cache_key] = self._clock()

        staleness = self._clock() - self._last_success_at[cache_key]
        if staleness >= self._max_staleness:
            raise ConfigStoreUnavailableError(
                f"App Configuration store {self._endpoint!r} has not refreshed "
                f"successfully in {staleness:.0f}s (max {self._max_staleness:.0f}s)"
            )
        return dict(provider)

    def _create_provider(self, key_filter, label_filter, trim_prefixes, cache_key):
        load, SettingSelector, DefaultAzureCredential = _import_sdk()
        try:
            return load(
                endpoint=self._endpoint,
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
                startup_timeout=self._startup_timeout,
                on_refresh_error=lambda error, key=cache_key: self._on_refresh_error(key, error),
            )
        except Exception as error:
            raise ConfigStoreUnavailableError(
                f"could not load configuration from {self._endpoint!r}: {error}"
            ) from error

    def _on_refresh_error(self, cache_key: tuple, error: Exception) -> None:
        _logger.warning(
            "app configuration refresh failed",
            extra={"event": "appconfig.refresh.failed"},
            exc_info=error,
        )
        if cache_key in self._pending_failure:
            self._pending_failure[cache_key] = True
```

`ping()` は変更しない(既に毎回フレッシュな接続を使い、`_providers`/`_last_success_at`
に触れない設計のまま)。

`_create_provider` のシグネチャが変わる(`cache_key` を追加で受け取る)ため、呼び出し箇所
(`select()` 内の1箇所)も合わせて更新する。

### 3.5 各サンプルの `source_*.py`

**`samples/01-shared-store-key-prefix/source_key_prefix.py`**(04も同型):

```python
def load(self, tenant_id: str) -> TenantConfig:
    def reload() -> dict[str, str]:
        ...  # 変更なし

    def close() -> None:
        # 共有プレフィックスの provider は他の全テナントも使うため閉じない。
        # このテナント自身の provider だけを閉じる。
        self._store.close(
            key_filter=f"{tenant_id}/*",
            trim_prefixes=[f"{tenant_id}/"],
        )

    return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload, close=close)
```

**`samples/02-shared-store-label/source_label.py`**:

```python
def close() -> None:
    self._store.close(key_filter="*", label_filter=tenant_id)
```

**`samples/03-store-per-tenant/source_store_per_tenant.py`**:

```python
def load(self, tenant_id: str) -> TenantConfig:
    store = self._store_for(tenant_id)

    def reload() -> dict[str, str]:
        ...  # 変更なし

    def close() -> None:
        # テナント専用ストアそのものを閉じる(共有ストアには触れない)。
        store.close(key_filter="*")

    return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload, close=close)
```

`samples/04-snapshot-references/source_snapshot_references.py` は01と同型なので同じ
`close` クロージャを追加する。

## 4. テスト設計

### 4.1 `tests/test_source.py`(または既存の場所)— `TenantConfig.close` 自体

- `close` が `None` の `TenantConfig` に対して `refresh()` を呼んでも `close` には触れない
  (既存の振る舞いに影響がないことの確認)。

### 4.2 `tests/test_cache.py` — evict/expire と close の連動

既存の `RecordingSource`/`FakeClock` パターンに `close` の呼び出し回数を数える仕組みを足す。

- LRU で evict されたエントリの `close()` が呼ばれる。evict されなかったエントリの
  `close()` は呼ばれない。
- TTL 切れで削除されたエントリの `close()` が呼ばれる。
- `close` が例外を送出しても `get()` 自体は失敗せず、ログに警告が残る
  (evict/expire の処理は止まらない)。
- `close` が `None` のエントリを evict/expire しても何も起きない(既存テストの回帰確認)。

### 4.3 `tests/test_azure_source.py` — `_FakeSdk`/`_FakeProvider` を拡張

`_FakeProvider` に「次の `refresh()` 呼び出しで `on_refresh_error` を発火させる」モードを
追加する:

```python
class _FakeProvider(dict):
    def __init__(self, values=None, on_refresh_error=None):
        super().__init__(values or {"LogLevel": "Warning"})
        self.refresh_calls = 0
        self.closed = False
        self._on_refresh_error = on_refresh_error
        self.fail_next_refresh = False

    def refresh(self):
        self.refresh_calls += 1
        if self.fail_next_refresh and self._on_refresh_error is not None:
            self._on_refresh_error(RuntimeError("refresh failed"))

    def close(self):
        self.closed = True
```

`_FakeSdk.load(**kwargs)` は `kwargs["on_refresh_error"]` を捕まえて `_FakeProvider` に
渡す(既存の `providers` リストからテストが provider を取り出して `fail_next_refresh` を
操作できるようにする)。

新規テスト:

- **R1**: `store.close(key_filter="tenant-a/*")` が該当 provider だけを `_providers` から
  取り除いて `.close()` を呼ぶ。共有プレフィックス用の別 provider には触れない
  (2つの異なる key_filter で select してから片方だけ close し、もう片方が
  `refresh_calls` でまだ生きていることを確認)。
- **R2 成功パス**: 連続する `select()` 呼び出しで `fail_next_refresh=False` のままなら、
  `max_staleness_seconds` をどれだけ待っても(`clock` を進めても)例外は出ない
  (毎回成功が `_last_success_at` を更新し続けるため)。
- **R2 失敗パス**: `fail_next_refresh=True` にした状態で `clock` を `max_staleness_seconds`
  分進めてから `select()` を呼ぶと `ConfigStoreUnavailableError` を送出する。
- **R2 既定値**: `AzureAppConfigurationStore(endpoint)`(`max_staleness_seconds` を渡さない)
  の既定値が `refresh_interval_seconds * 3` になっていることを確認する。
- **R2 部分障害からの回復**: 一度 `fail_next_refresh=True` で失敗した直後に
  `fail_next_refresh=False` に戻して(まだ `max_staleness_seconds` に達する前に)
  `select()` を呼ぶと、例外を出さず `_last_success_at` が更新される
  (一時的な失敗は許容されることの確認)。

### 4.4 各サンプルの既存テスト(`samples/0N-*/tests/test_*.py`)

`close` を明示的に検証するテストは必須ではない(フェイクストアの `close` は no-op のため、
挙動としては何も変わらない)。ただし `TenantConfig(..., close=close)` を渡すコード自体が
壊れていないことを、既存の `test_resolves_the_merged_config_for_each_tenant` 等が
引き続き通ることで間接的に確認する。

## 5. 受け入れ条件

- `uv run pytest` が新規テストを含め、Azure SDK なしで全件成功する(既存テスト数 + 新規分)。
- `tests/test_azure_source.py` の新規テストが `_FakeSdk`/`_FakeProvider` シームだけで
  R1・R2 双方の挙動(provider の個別 close、古さの上限による例外)を検証する。
- `FakeAppConfigurationStore.close()` の追加が既存3+1サンプルのどのテストの結果も
  変えない(no-op であることの確認)。
- ルート README の該当箇所(信頼性・パフォーマンス効率の節、既知の制約)を更新し、
  R1・R2 が対応済みであることと、その対応が実 Azure 接続に対しては未検証(SDK シームの
  検証に留まる)であることの両方を明記する。
- `docs/reviews/2026-09-06-independent-review.md` を直接書き換えることはしない
  (レビュー記録として残す)。対応状況はルート README の「第三者レビューの指摘事項」表の
  R1・R2 行を更新して反映する。
