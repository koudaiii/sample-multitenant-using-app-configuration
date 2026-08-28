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

**Free tier のストア数上限は、Microsoft の一次情報どうしで食い違っています。**

- [Azure subscription and service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-app-configuration):
  "3 stores per region per subscription"
- [App Configuration FAQ](https://learn.microsoft.com/azure/azure-app-configuration/faq#which-app-configuration-tier-should-i-use):
  "Each subscription is limited to **one** configuration store per region in the Free tier"
- [クイックスタート: ストアの作成](https://learn.microsoft.com/azure/azure-app-configuration/quickstart-azure-app-configuration-create):
  "Free tier: Limited to 3 stores per subscription"

上限が3なら、このパターンは共有ストアも1つ使うので Free では2テナントまでです。上限が1なら、
このパターンは共有ストアだけで Free tier を使い切り、テナント別ストアを1つも作れません。
**このリポジトリではどちらが正しいかを判定していません。** デプロイ前に自分のサブスクリプ
ションで実際の上限を確認してください。Standard 以上ならストア数は無制限です。ストアが増える
ぶんデプロイと運用の対象も増えます。

なお `main.bicep` はこの上限を検査しません。Free tier で上限を超えるテナント数を指定すると、
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

デプロイの出力に `sharedEndpoint` と、テナントごとの `tenantEndpoints` (`tenantId` / `endpoint`
の配列) が入っています。アプリはこの2つを別々の環境変数で受け取ります。01・02 が使う単数の
`APPCONFIG_ENDPOINT` とは違うので注意してください。

```bash
export APPCONFIG_SHARED_ENDPOINT=https://appcs-shared-xxxxxxxx.azconfig.io
export APPCONFIG_ENDPOINTS='{"tenant-a":"https://appcs-tenant-a-xxxxxxxx.azconfig.io","tenant-b":"https://appcs-tenant-b-xxxxxxxx.azconfig.io"}'
uv pip install -r ../../requirements-azure.txt
uv run flask --app app run --port 5003
```

## 実ストアにデータを入れる

このリポジトリのどこにも、実ストアへ設定値を書き込むコードはありません（`seed_store_per_tenant.py`
が書き込むのはメモリ上のフェイクストアだけです）。`main.bicep` が各ストアに付与するのは
**App Configuration Data Reader**（読み取り専用）のみで、しかも `disableLocalAuth: true`
のため接続文字列も使えません。つまり **記事どおりにデプロイして上の環境変数を設定しただけ
では、どのストアも空のまま**です。エラーは出ず、`/readyz` も `ready` を返します（共有ストア
の到達性しか見ないため）。

書き込むには、アプリの ID（`readerPrincipalId`）とは別に、**あなた自身の Entra ID** に
一時的に **App Configuration Data Owner** を、共有ストアと各テナントストアの**それぞれに**
付与し、`az login` した状態で書き込みます。

```bash
RG=<リソースグループ名>
SHARED_STORE=<sharedEndpoint のホスト名部分>       # 例: appcs-shared-xxxxxxxx
STORE_A=<tenant-a の endpoint のホスト名部分>       # 例: appcs-tenant-a-xxxxxxxx
STORE_B=<tenant-b の endpoint のホスト名部分>       # 例: appcs-tenant-b-xxxxxxxx
ME="$(az ad signed-in-user show --query id -o tsv)"

for STORE in "$SHARED_STORE" "$STORE_A" "$STORE_B"; do
  az role assignment create \
    --assignee "$ME" \
    --role "App Configuration Data Owner" \
    --scope "$(az appconfig show -g "$RG" -n "$STORE" --query id -o tsv)"
done

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
```

アプリ自身は `readerPrincipalId` に割り当てられた Data Reader のまま読み取るだけで動きます。
Data Owner が要るのはこの投入作業のときだけです。
