# 04 — スナップショット参照によるロールアウト制御

サンプル01(共有ストア + キープレフィックス)の**上に築く追加機能**のサンプルです。分離モデルの
4つ目ではありません — テナントの分け方はサンプル01と一切変わらず(`source_snapshot_references.py`
の選択ロジックは01の `KeyPrefixSource` とクラス名・docstring以外同一です)、ストア側の状態
(参照キーがどのスナップショットを指しているか)を変えるだけで配信内容が切り替わることを示します。

対象記事: [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration) ―
「Configuration rollout and rollback with snapshot references」節
参照: [Snapshot references](https://learn.microsoft.com/azure/azure-app-configuration/concept-snapshot-references)

## 記事の content checklist との対応

記事の提案(PR1)は4項目の content checklist と4項目の definition of done、
計8つのチェック項目を持ちます。前者2項目はコードで検証できるので、このサンプルの
どのテストが裏付けるかを示します。後者2項目(mechanicsをリンクに留める、`ms.date` の扱い)は
記事本文そのものの編集ルールであり、Pythonリポジトリであるこのサンプルの検証対象外です。

| checklist項目 | このサンプルでの検証 |
| --- | --- |
| 参照キーをテナントのキープレフィックス/ラベルでスコープし、テナント・コホート・スタンプが独立したスケジュールで設定を進められ、参照先を変えるだけでロールフォワード/ロールバックできる | `seed_snapshot_references.py`(`tenant-a/ConfigSnapshot` を `tenant_id` プレフィックス配下に配置) / `tests/test_snapshot_references.py::test_rollout_serves_the_new_snapshot_values` と `::test_repointing_the_reference_rolls_back_with_no_code_change` |
| スナップショット参照はテナント分離・認可境界を作らない。アクセスはストアレベルのまま、アプリの ID には参照先スナップショットの読み取り権限が必要 | `main.bicep`(`readerPrincipalId` に付与するのは **App Configuration Data Reader** のみ。追加ロールなし) / `tests/test_snapshot_references.py::test_tenant_isolation_is_preserved` |

`fake.py` 側の解決メカニクス(未解決・期限切れの黙殺、キー衝突順序)は
`tests/test_fake.py` の `test_*_snapshot_reference_*` 系テストが担保します。

## ストアの中身

`seed_snapshot_references.py` がサンプル01と同じ11件のキー・値に加えて、次を追加します。

| 対象 | 内容 |
| --- | --- |
| スナップショット `tenant-a-2026-08-01`(旧) | `LogLevel=Warning`, `Features:BetaDashboard=false` |
| スナップショット `tenant-a-2026-09-01`(新) | `LogLevel=Debug`, `Features:BetaDashboard=true`, `DisplayName=Tenant A (rollout)` |
| `tenant-a/ConfigSnapshot` | 新スナップショットを指す参照キー(＝ロールアウト中の状態でシード) |
| `tenant-b/ConfigSnapshot` | 存在しないスナップショット名 `tenant-b-missing` を指す参照キー(フォールバック実演用) |

新スナップショットの `DisplayName` は tenant-a が直接持つ `DisplayName=Tenant A` と衝突します。
参照キーは直接設定より後に書き込まれるため、`select()` の走査順序どおりスナップショット側が
勝ちます(`src/mtappconfig/fake.py` の `FakeAppConfigurationStore.select`)。

## ロールアウト/ロールバックの操作

新しいHTTPエンドポイントは追加していません(スコープを絞るため)。参照先の切り替えは、
対話的な Python から `set_snapshot_reference` を直接呼び出して行います。

```python
from seed_snapshot_references import build_store, TENANT_A_PREVIOUS_SNAPSHOT
store = build_store()
store.set_snapshot_reference("tenant-a/ConfigSnapshot", TENANT_A_PREVIOUS_SNAPSHOT)  # ロールバック
```

`tests/test_snapshot_references.py::test_repointing_the_reference_rolls_back_with_no_code_change`
が同じ手順(参照先を書き換えて `TenantConfig.refresh()` を呼ぶ)をテストとして担保しています。
README のスニペットとテストが同じ `build_store()` / `set_snapshot_reference()` API を指すので、
片方だけ更新されて食い違うということが起きにくくなっています。

## いつ選ぶか

テナント・テナントコホート・デプロイスタンプが、他と独立したスケジュールで設定をロールアウト/
ロールバックする必要があるとき。分離モデルの選択(01〜03のどれを使うか)とは独立した追加機能です。

## 動かす

```bash
uv run flask --app app run --port 5004
curl -s localhost:5004/t/tenant-a/api/config   # ロールアウト後の値(LogLevel=Debug)
curl -s localhost:5004/t/tenant-b/api/config   # フォールバック値(参照が解決できない)
```

## Azure にデプロイ

```bash
az deployment group create -g <rg> -f main.bicep -p readerPrincipalId=<managed-identity-object-id>
```

`readerPrincipalId` に付与されるのは **App Configuration Data Reader** のみです。スナップショットの
読み取りに追加のロールは要りません(同じ Data Reader で足ります)。実ストアへスナップショットや
参照キーを作成するコードはこのリポジトリのどこにもありません(サンプル01の「実ストアにデータを
入れる」と同じ制約です)。
