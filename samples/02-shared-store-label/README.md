# 02 — 共有ストア + ラベル

1つのストアを共有し、**ラベル**でテナントを区別します。キーにはプレフィックスを付けません。

## ストアの中身

`seed_label.py` が `src/mtappconfig/sampledata.py` の値をこのレイアウトで書き込みます。
11件全部を載せています（省略なし）。

| キー | 値 | ラベル |
| --- | --- | --- |
| `App:SupportEmail` | `support@contoso.example` | なし |
| `App:Version` | `1.4.2` | なし |
| `DisplayName` | `Tenant A` | `tenant-a` |
| `LogLevel` | `Warning` | `tenant-a` |
| `DatabaseName` | `db-tenant-a` | `tenant-a` |
| `Features:BetaDashboard` | `false` | `tenant-a` |
| `DisplayName` | `Tenant B` | `tenant-b` |
| `LogLevel` | `Debug` | `tenant-b` |
| `DatabaseName` | `db-tenant-b` | `tenant-b` |
| `Features:BetaDashboard` | `true` | `tenant-b` |
| `App:SupportEmail` | `vip@contoso.example` | `tenant-b`（共有値を上書き） |

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

## 実ストアにデータを入れる

このリポジトリのどこにも、実ストアへ設定値を書き込むコードはありません（`seed_label.py`
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
# ラベルなしの2件が共有設定、残りはラベル (--label) でテナントを区別
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "App:SupportEmail" --value "support@contoso.example"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "App:Version" --value "1.4.2"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "DisplayName" --label "tenant-a" --value "Tenant A"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "LogLevel" --label "tenant-a" --value "Warning"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "DatabaseName" --label "tenant-a" --value "db-tenant-a"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "Features:BetaDashboard" --label "tenant-a" --value "false"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "DisplayName" --label "tenant-b" --value "Tenant B"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "LogLevel" --label "tenant-b" --value "Debug"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "DatabaseName" --label "tenant-b" --value "db-tenant-b"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "Features:BetaDashboard" --label "tenant-b" --value "true"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "App:SupportEmail" --label "tenant-b" --value "vip@contoso.example"

export APPCONFIG_ENDPOINT="https://$STORE.azconfig.io"
uv pip install -r ../../requirements-azure.txt
uv run flask --app app run --port 5002
```

アプリ自身は `readerPrincipalId` に割り当てられた Data Reader のまま読み取るだけで動きます。
Data Owner が要るのはこの投入作業のときだけです。
