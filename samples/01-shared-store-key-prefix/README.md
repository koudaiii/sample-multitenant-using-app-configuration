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
ただし、レジストリ照合は不正な形式や未登録 ID を拒否する入力検証であり、**認可ではありません**。
本番では URL で呼び出し元が選んだテナント ID をそのまま信頼せず、認証済みコンテキストから
テナントを導出し、そのテナントへのアクセスを認可してから `TenantRegistry.resolve` へ渡します。

## いつ選ぶか

- 共有アプリケーション層で多数のテナントを捌く、一般的なマルチテナント構成
- ラベルをバージョニングや環境（dev/prod）の区別に使いたい場合

## メリット・デメリットとアンチパターン

### メリット

- ストアが1つで済むため、コスト・運用・監視の対象が最小限。テナント追加もキーを書くだけで
  完了し、インフラのデプロイが不要。
- 記事が既定として推奨するパターンで、Well-Architected 評価でも「低コスト・低複雑度」。
- `trim_prefixes` によりアプリからは常に同じキー名（`LogLevel` 等）に見えるため、
  テナントごとにコードを分ける必要がない。

### デメリット

- データ分離・性能分離が「低」。1つのストアを全テナントで共有するため、あるテナントの
  大量アクセスが他のテナントのリクエストクォータを圧迫し得る（noisy neighbor、
  [「リクエストクォータと geo-replication」](#リクエストクォータと-geo-replication)参照）。
- テナントごとに異なる CMK（顧客管理キー）を使えない（CMK はストア単位）。
- テナント ID の検証を誤ると、キーフィルタに `*` を渡すだけで全テナントの設定が漏える
  というセキュリティ上の急所が生まれる。`src/mtappconfig/tenants.py` のレジストリ照合に加え、
  認証済みコンテキストに基づくテナント認可が必要。

### 想定シナリオ

- 数十〜数百程度のテナントを、共有のアプリケーション層（同一デプロイ）で捌く一般的な SaaS。
- ラベルをバージョニングや環境（dev/stg/prod）の区別に温存しておきたい場合。

### アンチパターンシナリオ

- テナントごとに異なる CMK を要求される契約（金融・医療系の一部テナントなど）があるのに、
  このパターンのまま「後からテナントだけ移行すればいい」と先送りする。CMK はストア単位の
  ため、結局は [03（テナント別ストア）](../03-store-per-tenant/) への作り直しが必要になる。
- テナント ID の検証をスキップしてキーを組み立てる（例: URL パスをそのまま `key_filter` に
  渡す）、またはレジストリに存在するという理由だけでアクセスを許可する。入力検証と、
  認証済み主体がそのテナントへアクセスできるかの認可は別々に必要。

## 動かす

```bash
uv run flask --app app run --port 5001
curl -s localhost:5001/t/tenant-a/api/config
```

## Azure にデプロイ

```bash
az deployment group create -g <rg> -f main.bicep -p readerPrincipalId=<managed-identity-object-id>
```

これは **Azure-hosted execution** 用の権限です。`readerPrincipalId` にはホストするアプリの
マネージド ID のオブジェクト ID を渡します。Bicep はその ID に
**App Configuration Data Reader** を付与し、Azure 上のアプリは `DefaultAzureCredential`
を通してそのマネージド ID を使います（アプリのホスティングと ID の作成自体はこの Bicep の
範囲外です）。

## リクエストクォータと geo-replication

Standard ストアでは [geo-replication](https://learn.microsoft.com/azure/azure-app-configuration/howto-geo-replication)
を使うと、レプリカごとに独立したリクエストクォータを持てます。Python プロバイダーは
provider 2.5.0 はレプリカの自動検出・フェイルオーバーに加え、`load_balancing_enabled=True`
によるレプリカ間の負荷分散にも対応します。ただし、このアダプターは既定値 `False` のままで、
負荷分散は有効にしていません（SDK の未対応ではなく、このサンプルの選択です）。
そのため、この Python サンプルではレプリカ追加だけでクォータの実効容量は増えません。Premium
ストアにはリクエストクォータの上限がありません。ストレージ上限やテナント分割の都合で複数ストアが
必要な場合は、ストアを分けることを検討してください。

geo-replication は [noisy neighbor 問題](https://learn.microsoft.com/en-us/azure/architecture/antipatterns/noisy-neighbor/)
を防ぎません。1つの共有ストアを全テナントで使うこのパターンでは、特定テナントのリクエスト集中が
他のテナントの応答に影響し得ます。テナント単位のレート制限・クォータをアプリケーション側で適用し、
テナントごとのリクエスト使用量を監視してください。

このリポジトリのフェイクストア（`src/mtappconfig/fake.py`）はこの挙動を再現しません
（`request_count` は合計のみを追跡し、レプリカやテナント単位のクォータはモデル化していません）。
実ストアでの検証はこのサンプルの範囲外です。

## 実ストアにデータを入れる

このリポジトリのどこにも、実ストアへ設定値を書き込むコードはありません（`seed_key_prefix.py`
が書き込むのはメモリ上のフェイクストアだけです）。`main.bicep` が付与するのは
**App Configuration Data Reader**（読み取り専用）のみで、しかも `disableLocalAuth: true`
のため接続文字列も使えません。つまり **記事どおりにデプロイして `APPCONFIG_ENDPOINT` を
設定しただけでは、ストアは空のまま**です。エラーは出ず、`/readyz` も `ready` を返し、
`/t/tenant-a/api/config` は空の `values` を返します。

以下は **ローカル開発** の手順です。`DefaultAzureCredential` は `az login` でサインインした
開発者の資格情報を使います。その開発者に一時的な **App Configuration Data Owner** を付与し、
その権限でデータ投入とローカルアプリからの読み取りを行います。アプリ用の
`readerPrincipalId` に Data Owner を付与する手順ではありません。

```bash
# main.bicep の出力は endpoint（例: https://appcs-shared-xxxxxxxx.azconfig.io という URL）。
# az appconfig コマンドが要求するのはストア名なので、ホスト名部分だけを使う。
STORE=appcs-shared-xxxxxxxx
RG=<リソースグループ名>
ME="$(az ad signed-in-user show --query id -o tsv)"
STORE_SCOPE="$(az appconfig show -g "$RG" -n "$STORE" --query id -o tsv)"

# 自分に一時的な投入・読み取り権限を付与し、後で削除できるよう割り当て ID を保存する
OWNER_ASSIGNMENT_ID="$(az role assignment create \
  --assignee "$ME" \
  --role "App Configuration Data Owner" \
  --scope "$STORE_SCOPE" \
  --query id -o tsv)"

# RBAC の反映には最大約15分かかることがある。直後のコマンドが 403 なら待って再試行する。

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

テスト終了後にローカルアプリを停止し、保存した ID で開発者の一時的な Data Owner 割り当てを
削除します。

```bash
az role assignment delete --ids "$OWNER_ASSIGNMENT_ID"
```

Azure-hosted application は、`readerPrincipalId` に割り当てられた Data Reader のまま
マネージド ID で読み取ります。
