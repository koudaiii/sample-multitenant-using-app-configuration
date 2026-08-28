# 01 — 共有ストア + キープレフィックス

1つの App Configuration ストアを全テナントで共有し、キーの先頭にテナント ID を付けて
区別します。**記事が既定として推奨するパターン**です。

## ストアの中身

`seed_key_prefix.py` が `src/mtappconfig/sampledata.py` の値をこのレイアウトで書き込みます。
11件全部を載せています（省略なし）。

| キー | 値 | ラベル |
| --- | --- | --- |
| `_shared/App:SupportEmail` | `support@contoso.example` | なし |
| `_shared/App:Version` | `1.4.2` | なし |
| `tenant-a/DisplayName` | `Tenant A` | なし |
| `tenant-a/LogLevel` | `Warning` | なし |
| `tenant-a/DatabaseName` | `db-tenant-a` | なし |
| `tenant-a/Features:BetaDashboard` | `false` | なし |
| `tenant-b/DisplayName` | `Tenant B` | なし |
| `tenant-b/LogLevel` | `Debug` | なし |
| `tenant-b/DatabaseName` | `db-tenant-b` | なし |
| `tenant-b/Features:BetaDashboard` | `true` | なし |
| `tenant-b/App:SupportEmail` | `vip@contoso.example` | なし（共有値を上書き） |

## 中核のコード

```python
shared = store.select(key_filter="_shared/*", trim_prefixes=["_shared/"])
tenant = store.select(key_filter=f"{tenant_id}/*", trim_prefixes=[f"{tenant_id}/"])
return {**shared, **tenant}
```

`trim_prefixes` でプレフィックスを削るので、**アプリからは常に `LogLevel` という同じキー名に
見えます**。テナントごとにコードを分ける必要がありません。

共有設定のプレフィックスが `_shared/` と先頭にアンダースコアを持つのも意図的です。テナント ID
は `\A[a-z0-9][a-z0-9-]{1,30}[a-z0-9]\Z` にしかマッチしないため `_` で始まることはなく、
`shared` という名前のテナントが登録されても、このプレフィックスと衝突して全テナントの
共有設定を上書きしてしまうことがありません。

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

## 実ストアにデータを入れる

このリポジトリのどこにも、実ストアへ設定値を書き込むコードはありません（`seed_key_prefix.py`
が書き込むのはメモリ上のフェイクストアだけです）。`main.bicep` が付与するのは
**App Configuration Data Reader**（読み取り専用）のみで、しかも `disableLocalAuth: true`
のため接続文字列も使えません。つまり **記事どおりにデプロイして `APPCONFIG_ENDPOINT` を
設定しただけでは、ストアは空のまま**です。エラーは出ず、`/readyz` も `ready` を返し、
`/t/tenant-a/api/config` は空の `values` を返します。

書き込むには、アプリの ID（`readerPrincipalId`）とは別に、**あなた自身の Entra ID**
に一時的に **App Configuration Data Owner** を付与し、`az login` した状態で書き込みます。

```bash
STORE=<main.bicep の出力にあるストア名>  # 例: appcs-shared-xxxxxxxx
RG=<リソースグループ名>

# 自分に書き込み権限を付与する（アプリの managed identity に付与された
# Data Reader とは別の割り当てです）
az role assignment create \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "App Configuration Data Owner" \
  --scope "$(az appconfig show -g "$RG" -n "$STORE" --query id -o tsv)"

# 上の「ストアの中身」の11件を、あなた自身の Entra ID (--auth-mode login) で書き込む
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

export APPCONFIG_ENDPOINT="https://$STORE.azconfig.io"
uv pip install -r ../../requirements-azure.txt
uv run flask --app app run --port 5001
```

アプリ自身は `readerPrincipalId` に割り当てられた Data Reader のまま読み取るだけで動きます。
Data Owner が要るのはこの投入作業のときだけです。
