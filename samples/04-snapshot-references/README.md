# 04 — スナップショット参照によるロールアウト制御

サンプル01(共有ストア + キープレフィックス)の**上に築く追加機能**のサンプルです。分離モデルの
4つ目ではありません — テナントの分け方はサンプル01と一切変わらず(`source_snapshot_references.py`
の選択ロジック(共有プレフィックス選択 + テナントプレフィックス選択のマージ)は01の
`KeyPrefixSource` と同一です。クラス名・`name` 属性・docstring・一部コメントが異なります)、
ストア側の状態(参照キーがどのスナップショットを指しているか)を変えるだけで配信内容が
切り替わることを示します。

対象記事: [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration) ―
「Configuration rollout and rollback with snapshot references」節
参照: [Snapshot references](https://learn.microsoft.com/azure/azure-app-configuration/concept-snapshot-references)

## 記事の content checklist との対応

記事の提案(PR1)は4項目の content checklist と4項目の definition of done、
計8つのチェック項目を持ちます。8項目すべての扱いは次のとおりです。

- **content checklistのうち2項目はコードで検証できます。** 下の表がどのテストで
  裏付けているかを示します。
- **content checklistの残り2項目**(スナップショット参照そのもののmechanicsは本記事で
  説明しきらず [Snapshot references](https://learn.microsoft.com/azure/azure-app-configuration/concept-snapshot-references)
  へのリンクに留める、既存の分離パターン(01〜03)の説明と混同しないよう独立した節として
  書く、など)は記事本文の書き方そのものに関する項目です。
- **definition of doneの4項目**(front-matterの `ms.date` を更新する、レビュー担当者の
  承認を得る、既存の関連記事へのクロスリンクを追加する、用語の表記ゆれを確認する、など)は
  記事の執筆・レビュープロセスに関する項目です。

後者2種類、合わせて6項目は記事本文・執筆プロセスそのものの編集ルールであり、
Pythonのコードサンプルであるこのサンプルの検証対象外です(サイレントに無視しているのではなく、
対象外であることを明示しています)。

| checklist項目 | このサンプルでの検証 |
| --- | --- |
| 参照キーをテナントのキープレフィックス/ラベルでスコープし、テナント・コホート・スタンプが独立したスケジュールで設定を進められ、参照先を変えるだけでロールフォワード/ロールバックできる | `seed_snapshot_references.py`(`tenant-a/RolloutSnapshot` を `tenant_id` プレフィックス配下に配置) / `tests/test_snapshot_references.py::test_rollout_serves_the_new_snapshot_values` と `::test_repointing_the_reference_rolls_back_with_no_code_change` |
| スナップショット参照はテナント分離・認可境界を作らない。アクセスはストアレベルのまま、アプリの ID には参照先スナップショットの読み取り権限が必要 | `main.bicep`(`readerPrincipalId` に付与するのは **App Configuration Data Reader** のみ。追加ロールなし) / `tests/test_snapshot_references.py::test_tenant_isolation_is_preserved` |

`fake.py` 側の解決メカニクス(未解決・期限切れの黙殺、キー衝突順序)は
`tests/test_fake.py` の `test_*_snapshot_reference_*` 系テストが担保します。

## ストアの中身

`seed_snapshot_references.py` がサンプル01と同じ11件のキー・値に加えて、次を追加します。

| 対象 | 内容 |
| --- | --- |
| スナップショット `tenant-a-2026-08-01`(旧) | `LogLevel=Warning`, `Features:BetaDashboard=false` |
| スナップショット `tenant-a-2026-09-01`(新) | `LogLevel=Debug`, `Features:BetaDashboard=true`, `DisplayName=Tenant A (rollout)` |
| `tenant-a/RolloutSnapshot` | 新スナップショットを指す参照キー(＝ロールアウト中の状態でシード) |
| `tenant-b/RolloutSnapshot` | 存在しないスナップショット名 `tenant-b-missing` を指す参照キー(フォールバック実演用) |

新スナップショットの `DisplayName` は tenant-a が直接持つ `DisplayName=Tenant A` と衝突します。
実際の App Configuration とこのフェイクはどちらも、同名キーの衝突を**キー名の辞書式順序**で
解決します(書き込み順ではありません)。参照キー名 `RolloutSnapshot` は `DatabaseName` /
`DisplayName` / `Features:BetaDashboard` / `LogLevel` のいずれよりも辞書式順序で後ろに来るため、
スナップショット側の値が勝ちます(`src/mtappconfig/fake.py` の `FakeAppConfigurationStore.select`、
参照: [Snapshot references — key conflict resolution](https://learn.microsoft.com/azure/azure-app-configuration/concept-snapshot-references#key-conflict-resolution))。

## 重要な注意: 参照キーのスコープはスナップショットの中身をフィルタしない

`tenant-a/RolloutSnapshot` のように参照キーをテナントのプレフィックス配下に置くのは、
**「どの参照キーを読むか」を決めるだけ**です。参照が解決されたあと、**スナップショットの
中身は一切フィルタされずにそのままマージされます**。もし `tenant-a-2026-09-01` という
スナップショットの作成時に誤って `tenant-b/DatabaseName` のようなキーを含めてしまうと、
tenant-a の解決結果にそのままそのキーが現れます — これは実際の App Configuration の
仕様どおりの挙動であり、フェイク側のバグではないため、フェイクで再フィルターして
隠すこともしていません。

`tests/test_fake.py::test_a_snapshot_containing_a_foreign_key_merges_it_in_unfiltered` と
`tests/test_snapshot_references.py::test_a_snapshot_containing_another_tenants_keys_leaks_them_unfiltered`
がこの挙動を負例として検証しています。

したがって、**「正しくスコープされたスナップショットを作る」ことは呼び出し側(運用手順)の
責任**です。下の「実ストアにデータを入れる」の手順が `--filters '[{"key":"tenant-a/*"}]'`
を使って対象テナントのキーだけからスナップショットを切り出しているのはこのためです。

## ロールアウト/ロールバックの操作

新しいHTTPエンドポイントは追加していません(スコープを絞るため)。参照先の切り替えは、
対話的な Python から `set_snapshot_reference` を直接呼び出して行います。

**このスニペットは自己完結しています。** `build_store()` はこの Python プロセスだけが
持つメモリ上のフェイクストアを新しく作るため、`uv run flask --app app run --port 5004`
で別途起動しているプロセスの内部状態には一切影響しません。ロールアウト前後の値を
自分の目で確認したい場合は、下のコードをそのまま(例えば `python3` の対話モードや
1本のスクリプトとして)実行してください。

```python
from seed_snapshot_references import build_store, TENANT_A_PREVIOUS_SNAPSHOT
from source_snapshot_references import SnapshotReferenceSource

store = build_store()
config = SnapshotReferenceSource(store).load("tenant-a")
print("ロールアウト中:", config.values["LogLevel"])  # Debug

store.set_snapshot_reference("tenant-a/RolloutSnapshot", TENANT_A_PREVIOUS_SNAPSHOT)  # ロールバック
config.refresh()
print("ロールバック後:", config.values["LogLevel"])  # Warning
```

`tests/test_snapshot_references.py::test_repointing_the_reference_rolls_back_with_no_code_change`
が同じ手順(参照先を書き換えて `TenantConfig.refresh()` を呼ぶ)をテストとして担保しています。
README のスニペットとテストが同じ `build_store()` / `set_snapshot_reference()` API を指すので、
片方だけ更新されて食い違うということが起きにくくなっています。

`set_snapshot_reference` は、実際の App Configuration と同じ
`{"snapshot_name": "..."}` という JSON オブジェクトと
`application/json; profile="https://azconfig.io/mime-profiles/snapshot-ref"; charset=utf-8`
content-type を格納します。呼び出し側がスナップショット名だけを渡せるのはヘルパーの API 上の
簡略化であり、保存される表現はサービスと同じです(詳細は `src/mtappconfig/fake.py` と下の
「実ストアにデータを入れる」を参照)。

## 実ストアに対するオプトインのlive検証

`tests/test_snapshot_references.py`(フェイク)と `tests/test_live_snapshot_references.py`
(実ストア、`@pytest.mark.live`、既定でスキップ)の2本立てです。後者は
下の「Azure にデプロイ」と「実ストアにデータを入れる」の手順で用意した実ストアに対して、
参照解決・別テナント不変・`az appconfig kv set` による実際の書き換え後のrefresh検出・
ロールバック・ロールアウト状態への復帰を検証します
(読み取り権限「なし」の拒否だけは、権限を落とした2つ目のIDを用意していないため
未検証です)。

```bash
# 下の手順で main.bicep のデプロイとデータ投入を済ませ、
# STORE、OWNER_ASSIGNMENT_ID を設定した同じシェルで実行する
uv pip install -r requirements-azure.txt
export APPCONFIG_ENDPOINT="https://$STORE.azconfig.io"
export AZURE_SUBSCRIPTION_ID=<subscription-id>
uv run pytest samples/04-snapshot-references/tests/test_live_snapshot_references.py --run-live -v
az role assignment delete --ids "$OWNER_ASSIGNMENT_ID"
```

このリポジトリのこのコミット時点では、これらのテストは実Azureに対して**実行されていません**
(このリポジトリの開発環境に Azure サブスクリプションがないためです)。詳細な検証範囲・
未検証項目はテストファイル自体のモジュール docstring に記載しています。

## いつ選ぶか

テナント・テナントコホート・デプロイスタンプが、他と独立したスケジュールで設定をロールアウト/
ロールバックする必要があるとき。分離モデルの選択(01〜03のどれを使うか)とは独立した追加機能です。

## メリット・デメリットとアンチパターン

### メリット

- テナント・コホート・デプロイスタンプ単位で、他に影響を与えずに設定のロールアウト/
  ロールバックができる。参照先を切り替えるだけでよく、コード変更・再デプロイが不要。
- 01の分離モデル・RBAC(Data Readerのみ)をそのまま流用でき、追加のロールや新しい
  分離境界を持ち込まない。
- ロールバックが「前のスナップショットに参照を戻すだけ」という単純な操作になるため、
  障害時の切り戻しが速い。

### デメリット

- テナント分離・認可境界を変えない(=強化もしない)機能。分離自体の課題を解決するもの
  ではなく、01(または02/03)の上に載る追加の複雑さにすぎない。
- キー衝突の解決順序(実サービスでは辞書順)を正しく理解していないと、意図した値が
  ロールアウトされないという気づきにくい落とし穴がある(上の「ストアの中身」参照 —
  このリポジトリの最終レビューで実際に発見された問題です)。
- スナップショットの読み取りに追加の権限は不要だが、スナップショット自体の作成・管理は
  運用フローとして別途必要になる。

### 想定シナリオ

- 特定のテナント(またはコホート、デプロイスタンプ)だけ新しい設定セットを先行検証し、
  問題なければ他へ展開する段階的ロールアウト。
- 障害発生時に、影響を受けたテナントだけ即座に前の設定へロールバックしたい場合。

### アンチパターンシナリオ

- 参照キーの命名を、他の直接設定キーより辞書式順序で「後」にならないよう配置してしまう
  (例: `A` 始まりの参照キー名 vs `L` 始まりの直接キー)。この場合、実サービスでは参照が
  直接キーに上書きされ、ロールアウトが効かない。
- スナップショット参照をテナント分離の手段だと誤解する。参照はストア単位の認可境界を
  変えないため、機密データの分離目的では使えない。
- スナップショット作成時のフィルタ範囲を誤り、他テナントのキーを混入させる。参照キーの
  スコープは参照先の選択だけを決め、スナップショットの中身はフィルタしないため、
  混入したキーはそのまま解決結果に漏れる(上の「重要な注意」参照)。

## 動かす

以下のコマンドはリポジトリルートで実行します。

```bash
uv run flask --app samples/04-snapshot-references/app.py run --port 5004
curl -s localhost:5004/t/tenant-a/api/config   # ロールアウト後の値(LogLevel=Debug)
curl -s localhost:5004/t/tenant-b/api/config   # フォールバック値(参照が解決できない)
```

## Azure にデプロイ

以下のコマンドもリポジトリルートで実行します。

```bash
az deployment group create \
  --resource-group <rg> \
  --template-file samples/04-snapshot-references/main.bicep \
  --parameters readerPrincipalId=<managed-identity-object-id> \
  --query properties.outputs
```

`runId` を省略するとデプロイごとに新しい値が生成され、モジュールのデプロイ名・Log Analytics・
App Configuration ストア名が別の `nameSuffix` になります。出力の `deployedRunId` を次回
`--parameters runId=<deployedRunId>` として渡すと同じ一式を更新できます。
`sharedStoreName` は下の投入手順の `STORE`、`endpoint` は `APPCONFIG_ENDPOINT` に使います。
サービスプリンシパル以外へ Reader を割り当てる場合は `readerPrincipalType=User` または
`Group` も渡してください。

スナップショットは不変で、同じストア内の同名スナップショットを置き換えられません。同じ
スナップショット名で投入手順からやり直す場合は、`runId` を省略して新しい一式を作るか、
スナップショット名を変更してください。同じ `runId` は既存インフラの更新用であり、
スナップショットデータを初期化する指定ではありません。

`readerPrincipalId` に付与されるのは **App Configuration Data Reader** のみです。スナップショットの
読み取りに追加のロールは要りません(同じ Data Reader で足ります)。実ストアへスナップショットや
参照キーを作成するコードはこのリポジトリのどこにもありません(サンプル01の「実ストアにデータを
入れる」と同じ制約です)。手動での投入手順は次節を参照してください。

## 実ストアにデータを入れる

サンプル01と同じく、このリポジトリには実ストアへ書き込むコードはありません
(`seed_snapshot_references.py` が書き込むのはメモリ上のフェイクストアだけです)。
`main.bicep` が付与するのは **App Configuration Data Reader**(読み取り専用)のみで、
`disableLocalAuth: true` のため接続文字列も使えません。

書き込むには、アプリの ID(`readerPrincipalId`)とは別に、**あなた自身の Entra ID**
に一時的に **App Configuration Data Owner** を付与し、`az login` した状態で書き込みます
(サンプル01の同名節と同じ手順です)。

```bash
STORE=appcs-shared-xxxxxxxx
RG=<リソースグループ名>

OWNER_ASSIGNMENT_ID=$(az role assignment create \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "App Configuration Data Owner" \
  --scope "$(az appconfig show -g "$RG" -n "$STORE" --query id -o tsv)" \
  --query id -o tsv)
```

### 1. サンプル01と同じ11件のキー・値を書き込む

`_shared/` プレフィックスと `tenant-a/` / `tenant-b/` プレフィックスの構造は
サンプル01の「実ストアにデータを入れる」の11件と完全に同一です(`src/mtappconfig/sampledata.py`
が両サンプル共通のデータソースのため)。

```bash
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "_shared/App:SupportEmail" --value "support@contoso.example"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "_shared/App:Version" --value "1.4.2"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/DisplayName" --value "Tenant A"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/LogLevel" --value "Warning"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/DatabaseName" --value "db-tenant-a"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/Features:BetaDashboard" --value "false"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-b/DisplayName" --value "Tenant B"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-b/LogLevel" --value "Debug"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-b/DatabaseName" --value "db-tenant-b"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-b/Features:BetaDashboard" --value "true"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-b/App:SupportEmail" --value "vip@contoso.example"
```

### 2. 2つのスナップショットを作る

実際の `az appconfig snapshot create` は `create_snapshot(name, settings)` のように任意の
リテラル値からスナップショットを作れる訳ではなく、**フィルタに一致する既存のキー・値から**
スナップショットを切り出します。したがって「旧」「新」2つの内容のスナップショットを作るには、
tenant-a のキーをいったん旧の値にしてから旧スナップショットを切り出し、続けて新の値に更新して
から新スナップショットを切り出す、という順序が必要です(このフェイクの `create_snapshot` の
ようにスナップショット作成後もキーを自由な値に戻せるわけではない点に注意してください)。

```bash
# tenant-a を「旧」の値にしてから、旧スナップショットを切り出す
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/LogLevel" --value "Warning"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/Features:BetaDashboard" --value "false"
az appconfig snapshot create -n "$STORE" --auth-mode login \
  --name "tenant-a-2026-08-01" \
  --filters '[{"key":"tenant-a/*"}]'

# tenant-a を「新」の値へ更新してから、新スナップショットを切り出す
# (DisplayName はロールアウト後の値へ変える ── 直接キーとの衝突実演用)
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/LogLevel" --value "Debug"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/Features:BetaDashboard" --value "true"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/DisplayName" --value "Tenant A (rollout)"
az appconfig snapshot create -n "$STORE" --auth-mode login \
  --name "tenant-a-2026-09-01" \
  --filters '[{"key":"tenant-a/*"}]'

# tenant-a の DisplayName を「新」スナップショット作成前の直接値へ戻す
# (このサンプルのキー衝突実演は「直接設定 DisplayName=Tenant A」対「新スナップショットの
#  DisplayName=Tenant A (rollout)」の対立が前提のため)
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/DisplayName" --value "Tenant A"
```

### 3. 参照キーを設定する(実際のcontent-type・値の形)

フェイクの `set_snapshot_reference` も、次の CLI と同じ content-type と JSON の形で
参照キーを保存します。

```bash
az appconfig kv set -n "$STORE" --auth-mode login --yes \
  --key "tenant-a/RolloutSnapshot" \
  --content-type 'application/json; profile="https://azconfig.io/mime-profiles/snapshot-ref"; charset=utf-8' \
  --value '{"snapshot_name": "tenant-a-2026-09-01"}'
```

`--value` に渡すのは `{"snapshot_name": "..."}` という JSON オブジェクトです。
ロールバックするには、この同じキーへ `snapshot_name` を
`tenant-a-2026-08-01` に変えて `az appconfig kv set` を再実行します。

```bash
export APPCONFIG_ENDPOINT="https://$STORE.azconfig.io"
uv pip install -r requirements-azure.txt
uv run flask --app samples/04-snapshot-references/app.py run --port 5004
```

アプリ自身は `readerPrincipalId` に割り当てられた Data Reader のまま読み取るだけで動きます。
Data Owner は投入とliveテスト内のロールバック/復帰操作にだけ必要です。liveテストを実行しない
場合は投入後すぐ、実行する場合はテスト後に `az role assignment delete --ids
"$OWNER_ASSIGNMENT_ID"` で削除してください。
