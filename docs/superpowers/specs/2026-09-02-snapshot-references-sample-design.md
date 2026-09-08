# スナップショット参照エンジン設計書

- 作成日: 2026-09-02
- 更新日: 2026-09-08
- 対象: Topic 14 / PR #18 のフェイクストア実装
- 参照: [Snapshot references](https://learn.microsoft.com/azure/azure-app-configuration/concept-snapshot-references)

## 1. 目的と最終スコープ

本 Topic は `FakeAppConfigurationStore` に、Azure App Configuration のスナップショット参照を
テスト可能な形で追加する。対象は次の4ファイルだけである。

- `src/mtappconfig/fake.py`
- `tests/test_fake.py`
- 本設計書
- `docs/superpowers/plans/2026-09-04-snapshot-references-sample-plan.md`

実装するのは、実サービスと同じ参照表現、安全な参照値の解析、不変スナップショット、
辞書順による競合解決、未解決・期限切れ参照の黙殺、および参照選択後のスナップショット内容の
マージである。

`samples/04-snapshot-references/`、ルート README、Bicep、実 Azure provider、ロールアウト用
アプリケーションは後続 Topic の責務であり、本 Topic では作成・変更しない。

## 2. 実サービスと同じ参照表現

```python
SNAPSHOT_REFERENCE_CONTENT_TYPE = (
    'application/json; profile="https://azconfig.io/mime-profiles/snapshot-ref"; charset=utf-8'
)

FakeSetting(
    key="tenant-a/RolloutSnapshot",
    value='{"snapshot_name": "tenant-a-2026-09-01"}',
    content_type=SNAPSHOT_REFERENCE_CONTENT_TYPE,
)
```

`set_snapshot_reference()` は値を裸のスナップショット名として保存せず、
`{"snapshot_name": "<name>"}` という JSON オブジェクトへ直列化する。
`FakeSetting.content_type` のデフォルトは `None` とし、通常設定の既存契約を維持する。

`set_many()` は `FakeSetting` の `content_type` を失わずに保存する。これにより、実 SDK や
インポート処理から得た参照設定と同じ形をフェイクへ投入しても参照として解決できる。

参照値の解析は例外を外へ出さない。次の値はすべて「参照先なし」と同じ扱いで黙って無視する。

- JSON として不正な文字列
- JSON オブジェクト以外
- `snapshot_name` がないオブジェクト
- `snapshot_name` が文字列でないオブジェクト
- JSON は正しいが存在しないスナップショット名を指す参照

## 3. 不変スナップショット

```python
store.create_snapshot(
    "tenant-a-2026-09-01",
    {"LogLevel": "Debug"},
    retention_seconds=None,
)
```

`create_snapshot()` は入力 `Mapping` を呼び出し時にコピーする。呼び出し元が元の辞書を変更しても
登録済みスナップショットの内容は変わらない。

名前も不変である。同名スナップショットが既に存在する場合は
`ValueError("snapshot '<name>' already exists")` を送出し、既存内容を置き換えない。

`retention_seconds` は引き続きフェイク固有の近似である。実サービスはアーカイブ時点から保持期間を
測るが、このフェイクはアーカイブ状態を持たないため作成時点から測る。これは本番仕様の主張ではない。

## 4. 選択、解決、競合順序

`select()` はキーとラベルで参照設定そのものを選んだ後、設定の生キーを辞書順に処理する。
同じ出力キーが複数回マージされた場合は、辞書順で後の設定が勝つ。登録した時系列は勝敗を変えない。

### 4.1 参照キーが勝つ実行例

`tenant-a/RolloutSnapshot` は `tenant-a/LogLevel` より辞書順で後なので、参照を先に登録しても
スナップショットの `LogLevel` が勝つ。

```python
store = FakeAppConfigurationStore()
store.create_snapshot("snap-1", {"LogLevel": "Debug"})
store.set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")
store.set("tenant-a/LogLevel", "Warning")

assert store.select(
    key_filter="tenant-a/*",
    trim_prefixes=["tenant-a/"],
) == {"LogLevel": "Debug"}
```

### 4.2 直接キーが勝つ実行例

`tenant-a/EarlySnapshot` は `tenant-a/LogLevel` より辞書順で前なので、参照を後で登録しても
直接設定の `LogLevel` が勝つ。

```python
store = FakeAppConfigurationStore()
store.create_snapshot("snap-1", {"LogLevel": "Debug"})
store.set("tenant-a/LogLevel", "Warning")
store.set_snapshot_reference("tenant-a/EarlySnapshot", "snap-1")

assert store.select(
    key_filter="tenant-a/*",
    trim_prefixes=["tenant-a/"],
) == {"LogLevel": "Warning"}
```

## 5. 重要: 参照キーのスコープは内容フィルターでも認可でもない

> **警告:** `key_filter="tenant-a/*"` が制限するのは、どの参照キーを選ぶかだけである。
> 選ばれた参照が指すスナップショットの内容に同じフィルターを再適用してはならない。
> 参照キーのプレフィックスやラベルは、スナップショット内容のテナント分離も認可も提供しない。

例えば `tenant-a/RolloutSnapshot` が `tenant-b/DatabaseName` を含むスナップショットを指す場合、
そのキーも結果へマージされる。したがって、意図したキーだけをスナップショットへ含めることと、
ストアへのアクセスを適切な ID/RBAC で制御することが実際の分離境界である。フェイク側で内容を
再フィルターすると、この危険な実サービス挙動を隠すため禁止する。

`trim_prefixes` はマージされる各キーへ従来どおり適用するが、選択可否や認可判断には使わない。

## 6. テスト方針

`tests/test_fake.py` で次を検証する。

- 公式 content type と `snapshot_name` JSON オブジェクト
- `set_many()` が `content_type` を保持して参照を解決すること
- 不正 JSON、非オブジェクト、欠落・非文字列 `snapshot_name` の黙殺
- 存在しない参照と期限切れ参照の黙殺
- 入力辞書のコピーによる内容の不変性
- 同名作成が `ValueError` となり、既存内容が残ること
- 登録順を辞書順と逆にした勝ち・負け両ケース
- 参照キーのスコープがスナップショット内容をフィルターしないこと
- 参照に似た通常設定を参照として扱わないこと

## 7. 受け入れ条件

- focused fake-store tests、provider lifecycle 関連テスト、全テストが通る。
- `git diff --check origin/main...HEAD` が通る。
- 変更範囲が最終スコープの4ファイルだけである。
- 実サービス表現、辞書順、不変名、黙殺条件、スコープ警告がコード・テスト・文書で一致する。
