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

プレフィックスもラベルも要りません。**ストアそのものがデータとアクセス権限の境界**だからです。
これがこのパターンのデータ分離が「高」である理由です。

起動時に `validate_coverage(registry)` を呼び、ストアが未設定のテナントがあればその場で
失敗させます。設定漏れが最初のリクエストまで露見しない状態を作らないためです。

ただし、`TenantRegistry.resolve` と `validate_coverage` は不正な形式・未登録 ID・ストア割り当て
漏れを拒否する検証であり、**呼び出し元の認可ではありません**。本番では URL で選ばれた ID だけを
根拠にストアを選ばず、認証済みコンテキストからテナントを導出してアクセスを認可してください。

## いつ選ぶか

記事が挙げる採用理由は2つです。

- **テナントごとに異なる顧客管理キー (CMK) が必要な場合。** CMK は Standard または Premium
  ストアが必要で、ストア単位で設定されます。異なる CMK が必要なテナントごとに、Standard か
  Premium のストアを分けてデプロイしてください。ただし、**このリポジトリの Bicep は CMK を
  構成しません**。各ストアで CMK を有効にするには、ストア自身のマネージド ID、その ID への
  Key Vault キー権限（RBAC なら **Key Vault Crypto Service Encryption User**）、および
  [App Configuration の暗号化設定](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-customer-managed-keys)
  を別途構成する必要があります。
- **テナントが設定データの分離を要求する場合。** App Configuration のアクセス権限はストア単位
  でしか制御できないため、権限を分けるにはストアを分けるしかありません。

裏を返すと、共有アプリケーション層で全テナントを捌く構成なら、テナント別ストアの利点は
ほとんどありません。

## メリット・デメリットとアンチパターン

### メリット

- データ分離・性能分離が「高」。アクセス権限もストア単位で完全に分離できる。
- [CMK が必要なテナント](#いつ選ぶか)にはストアごとに設定できる（このリポジトリの Bicep
  自体は CMK を構成しない）。
- テナント専用ストアに保存したデータと、そのストア自体の障害範囲をテナント単位に分離できる
  （`/readyz` は共有ストアの到達性のみを見る設計）。

### デメリット

- テナント数だけストアが増えるため、デプロイ・運用・コストが「中〜高」
  （[コスト上の注意](#コスト上の注意)参照）。main.bicep はこの上限を検査しないため、Free
  tier で超えると Azure のクォータエラーで分かりにくい失敗になる。
- 起動時に `validate_coverage` で全テナントのストア設定を検査するため、テナント追加の
  たびにデプロイ操作が発生する（01/02 のようにキーを書くだけでは済まない）。

### 想定シナリオ

- テナントごとに異なる CMK が契約上必須、またはテナントが設定データの完全な分離を要求する
  場合。
- 少数の大口テナント（エンタープライズ契約など）を、コストをかけてでも分離したい場合。

### アンチパターンシナリオ

- 大多数の小口テナントに対してこのパターンをデフォルト採用する。テナント数に比例して
  デプロイ・運用コストが増え続け、[01](../01-shared-store-key-prefix/)/
  [02](../02-shared-store-label/) で十分だったはずの構成が過剰設計になる。
- CMK やデータ分離の要求が「一部の大口テナントだけ」なのに、全テナントを 03 に統一する。
  実際には 01/02（大多数）+ 03（要件のあるテナントのみ）のハイブリッド運用が現実的な
  落とし所であることが多い。

## コスト上の注意

- **Free tier のストア数上限は3（リージョン・サブスクリプションごと）。** 共有ストアも1つ
  使うため、同じリージョンでは**テナント専用ストアを2つまで（＝2テナントまで）**しか構成
  できません。出典: [Azure subscription and service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-app-configuration)
- Developer/Standard/Premium には上限がありませんが、ストアごとに課金・運用対象が増えます。
  ストア単位の SLA が必要な本番では Standard か Premium を選んでください。
- `main.bicep` はこの上限を検査しないため、Free tier で超過すると Azure のクォータエラーで
  分かりにくく失敗します。

> [!NOTE]
> テナント専用ストアでデータとストア障害の範囲は分離されますが、リクエスト処理は完全には
> 分離されません。詳細は[ルート README のタイムアウトの説明](../../README.md#タイムアウトは処理全体の締め切りではない)を参照してください。

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

これは **Azure-hosted execution** 用の権限です。`readerPrincipalId` にはホストするアプリの
マネージド ID のオブジェクト ID を渡します。Bicep は共有ストアと各テナントストアでその ID に
**App Configuration Data Reader** を付与し、Azure 上のアプリは
`DefaultAzureCredential` を通してそのマネージド ID を使います（アプリのホスティングと
ID の作成自体はこの Bicep の範囲外です）。

デプロイの出力に `sharedEndpoint` と、テナントごとの `tenantEndpoints` (`tenantId` / `endpoint`
の配列) が入っています。アプリはこの2つを別々の環境変数で受け取ります。01・02 が使う単数の
`APPCONFIG_ENDPOINT` とは違うので注意してください。

## 実ストアにデータを入れる

投入手順の一般的な流れ（空のストアのまま動いてしまう理由、一時的な Data Owner 付与、
`disableLocalAuth: true` の制約）は [01 の同名節](../01-shared-store-key-prefix/#実ストアにデータを入れる)
と同じです。03 固有の違いは、**共有ストアと各テナントストアの3つそれぞれ**に Data Owner を
付与し、プレフィックスもラベルも使わずストアそのものでキーを書き分ける点です。

```bash
RG=<リソースグループ名>
DEPLOYMENT=<上で実行したデプロイ名>
SHARED_STORE=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query properties.outputs.sharedStoreName.value -o tsv)
STORE_A=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.tenantStoreNames.value[?tenantId=='tenant-a'].name | [0]" -o tsv)
STORE_B=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.tenantStoreNames.value[?tenantId=='tenant-b'].name | [0]" -o tsv)
ME="$(az ad signed-in-user show --query id -o tsv)"
OWNER_ASSIGNMENT_IDS=()

for STORE in "$SHARED_STORE" "$STORE_A" "$STORE_B"; do
  STORE_SCOPE="$(az appconfig show -g "$RG" -n "$STORE" --query id -o tsv)"
  OWNER_ASSIGNMENT_IDS+=("$(az role assignment create \
    --assignee "$ME" \
    --role "App Configuration Data Owner" \
    --scope "$STORE_SCOPE" \
    --query id -o tsv)")
done

# RBAC の反映には最大約15分かかることがある。直後のコマンドが 403 なら待って再試行する。

# 共有ストア: App:SupportEmail, App:Version
az appconfig kv set -n "$SHARED_STORE" --auth-mode login --yes --key "App:SupportEmail" --value "support@contoso.example"
az appconfig kv set -n "$SHARED_STORE" --auth-mode login --yes --key "App:Version" --value "1.4.2"

# tenant-a のストア
az appconfig kv set -n "$STORE_A" --auth-mode login --yes --key "DisplayName" --value "Tenant A"
az appconfig kv set -n "$STORE_A" --auth-mode login --yes --key "LogLevel" --value "Warning"
az appconfig kv set -n "$STORE_A" --auth-mode login --yes --key "DatabaseName" --value "db-tenant-a"
az appconfig kv set -n "$STORE_A" --auth-mode login --yes --key "Features:BetaDashboard" --value "false"

# tenant-b のストア（App:SupportEmail は共有値の上書き。プレフィックスもラベルも
# 要らない — ストアそのものが境界なので、テナントのストアに書けば上書きになる）
az appconfig kv set -n "$STORE_B" --auth-mode login --yes --key "DisplayName" --value "Tenant B"
az appconfig kv set -n "$STORE_B" --auth-mode login --yes --key "LogLevel" --value "Debug"
az appconfig kv set -n "$STORE_B" --auth-mode login --yes --key "DatabaseName" --value "db-tenant-b"
az appconfig kv set -n "$STORE_B" --auth-mode login --yes --key "Features:BetaDashboard" --value "true"
az appconfig kv set -n "$STORE_B" --auth-mode login --yes --key "App:SupportEmail" --value "vip@contoso.example"

export APPCONFIG_SHARED_ENDPOINT="https://$SHARED_STORE.azconfig.io"
export APPCONFIG_ENDPOINTS="{\"tenant-a\":\"https://$STORE_A.azconfig.io\",\"tenant-b\":\"https://$STORE_B.azconfig.io\"}"
uv pip install -r ../../requirements-azure.txt
uv run flask --app app run --port 5003
```

ストア名はテナント ID とリソース名シードのハッシュから導出されます（共有は
`appcs-shared-<resource-hash>`、専用は `appcs-<tenant-hash>-<resource-hash>`）。
`tenant-a` を名前へ直接埋め込む形式ではないため、名前を推測せずデプロイ出力を使ってください。

テスト終了後は [01 と同じ手順](../01-shared-store-key-prefix/#実ストアにデータを入れる)で、
保存した3つの Data Owner 割り当てをすべて削除します。

```bash
for ID in "${OWNER_ASSIGNMENT_IDS[@]}"; do
  az role assignment delete --ids "$ID"
done
```

Azure-hosted application は、`readerPrincipalId` に割り当てられた Data Reader のまま
マネージド ID で各ストアを読み取ります。
