# サンプル04「スナップショット参照」設計書

- 作成日: 2026-09-02
- 目的: `architecture-center-pr/report.md` の **PR1(スナップショット参照によるロールアウト制御)**
  が提案する記事本文の技術的主張を、動くコードとテストで検証できるようにする。
- 対象記事: [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration)
- 参照: [Snapshot references](https://learn.microsoft.com/azure/azure-app-configuration/concept-snapshot-references)
- 前提設計書: [[2026-08-28-multitenant-app-configuration-design]]

## 1. 背景とスコープ

PR1 は既存記事に次のサブセクションを追加する提案である。

> テナント・テナントコホート・デプロイスタンプごとにスコープしたキーが不変スナップショットを
> 指すようにし、参照先を変えるだけでリフレッシュ時に新しい設定セットへ移行できる
> (コード変更・再デプロイ不要)。テナント分離/認可境界は変わらない。

この主張は本リポジトリのサンプル01(共有ストア + キープレフィックス)の**上に築く追加機能**であり、
01〜03のような独立した分離モデルではない。したがって新しい分離パターンを1つ増やすのではなく、
**01と同じ選択ロジックの上で、参照解決という店舗(ストア)側の挙動だけを追加検証する**サンプルを作る。

### スコープ外

- `azure_source.py`(実 Azure バインディング)への変更。スナップショット参照の解決は
  実運用では configuration provider(SDK)側が自動的に行うため、`load()` への渡し方は
  サンプル01と変える必要がない。この判断自体は本設計書の対象だが、コード変更はしない。
- ルート README の01〜03比較表への統合。04は分離モデルの4つ目ではないため、表の外側で
  「01の上に築くロールアウト制御」として別枠に記載する。
- `TenantConfigSource` / `TenantConfig` / `cache.py` / `webapp.py` の変更。スナップショット参照は
  フェイクストア(`fake.py`)だけに閉じ込め、コアの契約・既存3サンプルの挙動には触れない。

## 2. コア変更 — `src/mtappconfig/fake.py`

### 2.1 データモデル

```python
SNAPSHOT_REFERENCE_CONTENT_TYPE = "application/vnd.microsoft.appconfig.snapshotreference+json"

@dataclass(frozen=True)
class FakeSetting:
    key: str
    value: str
    label: str | None = None
    content_type: str | None = None   # 追加

@dataclass(frozen=True)
class _Snapshot:
    settings: dict[str, str]
    created_at: float
    retention_seconds: float | None
```

`content_type` のデフォルトは `None` なので、既存の `.set()` / `.select()` を使う3サンプルと
既存テストの挙動は一切変わらない。

### 2.2 `FakeAppConfigurationStore` への追加

- `__init__` に `clock: Callable[[], float] = time.monotonic` を追加(`cache.py` と同じ流儀。
  保持期限切れをテストで決定的に再現するため)。
- `create_snapshot(name: str, settings: Mapping[str, str], *, retention_seconds: float | None = None) -> None`
  — 呼び出し時点の値を `dict(settings)` としてコピーし、不変スナップショットとして登録する。
- `set_snapshot_reference(key: str, snapshot_name: str, label: str | None = None) -> None`
  — 通常のキー・値と同じ `_settings` 辞書に、`content_type=SNAPSHOT_REFERENCE_CONTENT_TYPE`
  を持つ `FakeSetting` として登録する。**参照キー自体は他のキーと同列に扱われる**
  (PR1 チェックリスト1「スナップショット参照は通常のキー・値」に対応)。
- `_resolve_snapshot(name: str) -> dict[str, str] | None`
  — 未登録、または `retention_seconds` が設定されていて
  `clock() - created_at >= retention_seconds` なら `None`(＝解決不能)を返す。

### 2.3 `select()` の拡張

反復中の設定が参照(`content_type == SNAPSHOT_REFERENCE_CONTENT_TYPE`)なら:

- 解決できたら、スナップショットの各キー・値を(`trim_prefixes` を適用したうえで)
  `selected` にマージする。マージは Python 辞書の代入なので、**後から処理された設定が
  同名キーを上書きする**。`_settings` は挿入順を保持する通常の `dict` であり、走査順序が
  そのまま「provider の選択順序」を模す(PR1 チェックリスト5)。
- 解決できなければ、**このキー自体を `selected` に含めず、例外も送出しない**。
  他の(直接設定された)キー・値だけが返る(PR1 チェックリスト7の「providerは解決不能な参照を
  黙って無視し、他の選択済みキー値にフォールバックする」を再現)。

非参照の設定に対する既存の分岐は変更しない。

## 3. 新規サンプル `samples/04-snapshot-references/`

### 3.1 `source_snapshot_references.py`

サンプル01の `KeyPrefixSource` と**選択ロジックを意図的に同一**にする(共有プレフィックス選択 +
テナントプレフィックス選択のマージ)。差分はクラス名とdocstringのみ。この一致自体が
「参照先スナップショットを切り替えるだけでコード変更なしにロールアウトできる」という
PR1 の主張を裏付ける ── アプリ側のコードは01から一切変わっていないのに、ストアの状態
(参照先)を変えるだけで配信内容が変わることをテストと README で示す。

### 3.2 `seed_snapshot_references.py`

`mtappconfig.sampledata` の `SHARED_SETTINGS` / `TENANT_SETTINGS` を土台にしつつ、
ストアへ以下を追加する。

- `tenant-a` 用に2つのスナップショットを作成する:
  - `tenant-a-2026-08-01`(旧): `LogLevel=Warning`, `Features:BetaDashboard=false`
    (`TENANT_SETTINGS["tenant-a"]` と同じ値)
  - `tenant-a-2026-09-01`(新): `LogLevel=Debug`, `Features:BetaDashboard=true`
    に加え、キー衝突実演用の `DisplayName=Tenant A (rollout)` を追加
  - `tenant-a/ConfigSnapshot` という参照キーを新スナップショットへ向ける(＝ロールアウト中)。
- `tenant-b` の参照キー `tenant-b/ConfigSnapshot` は、**存在しないスナップショット名**
  (`tenant-b-missing`)を指す。フォールバック経路の実演用。
- 参照キーのキー名(`ConfigSnapshot`)は `tenant_id` プレフィックス配下に置くことで、
  01と同じ `key_filter=f"{tenant_id}/*"` 選択に自然に含まれる。

### 3.3 ロールアウト/ロールバックの操作面

新しいHTTPエンドポイントも新しいスクリプトファイルも追加しない(スコープを絞るため)。代わりに
README に、対話的に実行できる Python スニペットを掲載する:

```python
from seed_snapshot_references import build_store
store = build_store()
store.set_snapshot_reference("tenant-a/ConfigSnapshot", "tenant-a-2026-08-01")  # ロールバック
```

`tests/test_snapshot_references.py` の該当テストが、同じ手順(参照先を書き換えて
`config.refresh()` を呼ぶ)をコードとして担保する。README のスニペットとテストは同じ
`build_store()` / `set_snapshot_reference()` API を指すので、片方だけ更新されて食い違う
ということが起きにくい。

### 3.4 `app.py` / `main.bicep`

01と同型。共有ストア1つ、`readerPrincipalId` に **App Configuration Data Reader** のみ付与。
スナップショットの読み取りに追加のロールは不要(同じ Data Reader で足りる)ことを README に明記する。

### 3.5 テスト — `samples/04-snapshot-references/tests/test_snapshot_references.py`

| ケース | 検証内容 | 対応する PR1 チェックリスト項目 |
| --- | --- | --- |
| ロールアウト成功 | tenant-a の解決結果に新スナップショットの値が反映される | 1, 2 |
| ロールバック | 参照先を旧スナップショットに戻し `refresh()` すると値が戻る | 3 |
| 未解決の参照 | tenant-b は例外を送出せず、直接設定されたテナント値だけが返る(参照キー自体は結果に現れない) | 6, 7 |
| 期限切れスナップショット | `retention_seconds` を短く設定し、注入した `clock` を進めると同様にフォールバックする | 6 |
| キー重複の勝敗順 | 直接設定したキーがスナップショット内の同名キーを上書きする(逆順のケースも1本) | 5 |
| テナント分離が保たれる | tenant-a のスナップショット内容が tenant-b の解決結果に一切現れない。無効なテナントIDは `TenantRegistry` に拒否される(既存3サンプルと同じ経路) | 4 |
| HTTP経路 | `/t/tenant-a/api/config` は200で新スナップショット値を返す。`/t/tenant-b/api/config` は200でフォールバック値を返す(500にならない) | 7 |

`fake.py` 側の単体テストは `tests/test_fake.py` に追記する(`create_snapshot` /
`set_snapshot_reference` / 未解決・期限切れの黙殺 / キー重複順序)。

### 3.6 `README.md`

PR1 の content checklist 8項目をそのまま節見出しにし、各節で「このサンプルのどのテストが
その主張を裏付けるか」をファイル名・テスト関数名で明示する対応表を置く。これが
「PR1 を確認できる」の実体になる。加えて:

- 01との関係(独立した分離パターンではなく、その上の追加機能)を明記。
- 分離境界は変わらないこと、必要な RBAC ロールは Data Reader のみであることを明記。
- ロールアウト/ロールバックの手順(3.3のスクリプト)を掲載。

## 4. ルート README の変更

- 比較表(01/02/03)はそのまま。表の下に新しい節「ロールアウト制御(01の上に築く追加機能)」を作り、
  04へのリンクと1〜2文の要約を追加する。
- 「既知の制約」節に、スナップショット参照の解決はSDK側(provider)に委譲しており
  `azure_source.py` 側は変更していない旨と、その未検証性(既存の `azure_source.py` 全体の
  制約に準ずる)を一文で追記する。

## 5. 受け入れ条件

- `uv run pytest` で新規テストを含む全テストが Azure SDK なしに通過する。
- 既存3サンプルのテスト・コアテストが無変更のまま通過する(回帰なし)。
- サンプル04の README が PR1 の content checklist 8項目すべてに対応する節を持つ。
- `fake.py` の変更が加法的である(既存の `.set()` / `.select()` 呼び出しの挙動を変えない)ことを
  既存 `tests/test_fake.py` の全件通過で担保する。
