# provider ライフサイクルと古さの上限 設計書

- 状態: Topic 11 / PR #15 で実装済み
- 目的: テナントキャッシュからエントリを削除するとき、対応する Azure SDK provider
  も破棄し、次回ロードを新しい provider で開始する
- 対象:
  - `src/mtappconfig/source.py`
  - `src/mtappconfig/cache.py`
  - `src/mtappconfig/fake.py`
  - `src/mtappconfig/azure_source.py`
  - samples 01-03 の `source_*.py`
  - `tests/test_cache.py`
  - `tests/test_azure_source.py`
  - samples 01-03 の focused tests

## 1. 問題

`AzureAppConfigurationStore.select()` は、次の問い合わせ条件をキーに SDK provider を
再利用する。

```python
(key_filter, label_filter, tuple(trim_prefixes))
```

外側の `TenantConfigCache` が TTL 切れや LRU eviction でテナントを削除しても、
Azure 側の `_providers` に同じ provider が残れば、次回ロードは新しい接続を作らず、
古い provider を再利用する。

この状態では、アプリケーション側キャッシュの件数上限と TTL が SDK provider の
ライフサイクルに反映されない。

## 2. 採用する設計

### 2.1 `TenantConfig.close`

`TenantConfig` に任意の close callback を持たせる。

```python
@dataclass
class TenantConfig:
    tenant_id: str
    values: Mapping[str, str]
    reload: Callable[[], Mapping[str, str]] | None = field(default=None, repr=False)
    close: Callable[[], None] | None = field(default=None, repr=False)
```

`close=None` を既定値とし、破棄対象を持たない既存 source との互換性を保つ。

### 2.2 cache expiry / eviction

`TenantConfigCache` は次の順序で処理する。

TTL 切れ:

1. 対象 entry の `config.close()` を安全に呼ぶ。
2. entry をキャッシュから削除する。
3. expiration を記録する。
4. `source.load()` で新しい `TenantConfig` を作る。

LRU eviction:

1. 最も古い entry を ordered map から取り除く。
2. 取り除いた entry の `config.close()` を安全に呼ぶ。
3. eviction を記録する。

close callback が例外を送出した場合は警告ログを残して処理を続行する。古い
provider の破棄失敗によって、新しいテナントのロードやキャッシュの縮小を失敗させない。

### 2.3 store の共通インターフェース

fake と Azure の両方が次のメソッドを持つ。

```python
def close(
    self,
    key_filter: str = "*",
    label_filter: str | None = None,
    trim_prefixes: Sequence[str] = (),
) -> None:
    ...
```

`FakeAppConfigurationStore.close()` は no-op とする。

`AzureAppConfigurationStore.close()` は `select()` と同じ query tuple を作り、
`_providers` から該当 provider を `pop` してから、その provider の `close()` を呼ぶ。
存在しない query の close は no-op とする。

provider を辞書から先に削除するため、同じ query に対する次回 `select()` は必ず SDK
loader を呼び、新しい provider を作る。

## 3. sample ごとの所有範囲

### 3.1 sample 01: key prefix

テナント固有 provider:

```python
store.close(
    key_filter=f"{tenant_id}/*",
    trim_prefixes=[f"{tenant_id}/"],
)
```

共有 prefix の provider は他テナントも利用するため閉じない。

### 3.2 sample 02: label

テナント固有 provider:

```python
store.close(key_filter="*", label_filter=tenant_id)
```

label なしの共有 provider は閉じない。

### 3.3 sample 03: store per tenant

テナント専用 store の provider:

```python
tenant_store.close(key_filter="*")
```

共有 store の provider は閉じない。

## 4. provider load contract

`AzureAppConfigurationStore` と SDK loader の境界は `_import_sdk` seam を使ってテストする。
main provider を初回ロードするとき、次を渡す。

- 検証済み endpoint
- credential object
- key filter と label filter を持つ selector
- trim prefixes
- `refresh_enabled=True`
- 構成された refresh interval
- 構成された startup timeout（操作間で確認する再試行予算であり、処理時間の上限ではない）
- `connection_timeout` / `read_timeout` / `timeout` / `retry_total` / `retry_backoff_max`

credential は fake の型と同一性で存在を確認し、Azure SDK 内部の具象型には依存しない。
ストア単位で1つを所有して query と probe で共有する。query の close では閉じず、
`close_all()` / context manager 終了 / 通常プロセス終了で1回だけ閉じる。
失敗した load が返さない provider の HTTP 資源も、明示所有する transport で cleanup する。
失敗時に生存 provider がなければ資格情報も閉じ、既存 provider があれば保持する。
callback の refresh エラーは外側キャッシュへ送出し、tenant context と失敗件数を記録する。

## 5. テスト契約

### Cache

- LRU eviction は追い出したテナントだけを close する。
- TTL expiry は期限切れテナントを close してから再ロードする。
- close の例外は eviction / expiry を失敗させない。
- `close=None` は有効である。

### Azure adapter

- exact query の close は該当 provider だけを閉じる。
- shared query の provider は残る。
- close 済み query の次回 select は新しい provider をロードする。
- 未ロード query の close は no-op である。
- main provider load の引数が SDK seam に正しく渡る。
- full close は provider の close 失敗後も残りの provider と資格情報を閉じる。
- query close、probe、失敗した load が他の provider の資格情報を閉じない。
- `on_refresh_error` を呼ぶ SDK double で tenant 付きの失敗記録と interval no-op を検証する。

### Samples

- sample 01 は tenant prefix query だけを close する。
- sample 02 は tenant label query だけを close する。
- sample 03 は tenant 専用 store だけを close する。

## 6. 古さに関する保証

テナント固有 provider は外側キャッシュの TTL 切れ時に close・削除される。次回ロードは
新しい provider を作るため、同じ tenant provider を無期限に再利用しない。

共有 provider は複数テナントが利用するため、1テナントの expiry / eviction では閉じない。
したがって、共有 provider の古さはテナント単位の TTL では制限されない。

## 7. concurrency と障害時の trade-off

`TenantConfigCache.get()` は、entry の確認、close、`source.load()`、LRU 更新までを
1つの global lock 内で実行する。

TTL 切れ後の新規 provider load もこの lock 内で行われる。ストアが遅い、または利用不可の
場合、load が終了するまで別テナントの `get()` も待機する。`startup_timeout` は操作間で確認する
再試行予算であり、資格情報取得・HTTP 呼び出しを中断しないため、経過時間は予算を超え得る。
transport の接続/読み取り待ちと retry policy のオプションも設定するが、`Retry-After`、複数操作、
DNS・ロック待ちまで含むハードな締め切りではない。probe も同じ制約を持つ。
具体的な値と provider 2.5.0 のソース参照は [ルートREADME](../../../README.md#タイムアウトは処理全体の締め切りではない) に記載する。

この設計は cold miss の重複ロードを防ぐ一方、テナント間の待ち時間を分離しない。
より強い分離が必要な実運用では、tenant ごとの lock などを検討する。

## 8. 受け入れ条件

- TTL expiry と LRU eviction が tenant の close callback を呼ぶ。
- Azure adapter が exact query の provider だけを削除・close する。
- 同じ query の次回 select が新しい provider を作る。
- sample 01-03 が shared provider を閉じない。
- close 失敗がキャッシュ処理を停止させない。
- provider load contract が SDK seam で検証される。
- focused tests と full Python suite が成功し、`git diff --check` が clean である。
