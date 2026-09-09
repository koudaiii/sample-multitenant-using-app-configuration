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

`tenant_id` は `TenantRegistry.resolve` で形式と登録有無を検証しますが、これは**認可では
ありません**。本番では URL で呼び出し元が選んだラベルをそのまま信頼せず、認証済みコンテキスト
からテナントを導出し、そのテナントへのアクセスを認可してからラベルフィルタへ渡します。

## トレードオフ

記事は**テナント識別にはキープレフィックス（パターン01）を推奨**しています。ラベルをテナントに
使ってしまうと、ラベル本来の用途であるバージョニングや環境の区別に使えなくなるためです。
`tests/test_label.py::test_the_label_is_spent_on_tenancy` がこの制約を実際に示しています。

## いつ選ぶか

- **テナントごとにアプリケーションをデプロイしている場合。** 各デプロイが自分のラベルだけを
  読めばよく、ラベルフィルタが自然に効きます。
- それ以外では 01 を選んでください。

## メリット・デメリットとアンチパターン

### メリット

- 01 と同じく共有ストア1つで済み、コスト・運用が最小限。
- テナントごとに別々のアプリケーションをデプロイしている構成では、各デプロイが自分の
  ラベルだけを読めばよく、`label_filter` が自然に効く。

### デメリット

- ラベルをテナント識別に使うと、ラベル本来の用途（バージョニングや環境の区別）に
  使えなくなる（`tests/test_label.py::test_the_label_is_spent_on_tenancy` が示すとおり）。
- ラベルフィルタを省略すると「ラベルなし」の設定しか返らないという既定挙動を全選択箇所で
  徹底する必要があり、指定漏れという実装ミスが起きやすい。
- 01 と同様、データ・性能分離は「低」で CMK もストア単位。

### 想定シナリオ

- テナントごとに別々のアプリケーションインスタンス／デプロイを持ち、各インスタンスが
  自分のラベルだけを読めばよい構成。

### アンチパターンシナリオ

- 同一アプリケーションが複数テナントを横断的に捌く構成（01 と同じユースケース）なのに、
  あえてラベルを使う。ラベルを環境・バージョニング用に使えなくなるだけで、01 に対する
  利点がない。
- ラベルフィルタの指定を一部のコードパスで忘れる。既定の「ラベルなし」挙動により、
  意図せず共有設定だけが返り（テナント設定が反映されない）、気づきにくい不具合になる。

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

このリポジトリのどこにも、実ストアへ設定値を書き込むコードはありません（`seed_label.py`
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

テスト終了後にローカルアプリを停止し、保存した ID で開発者の一時的な Data Owner 割り当てを
削除します。

```bash
az role assignment delete --ids "$OWNER_ASSIGNMENT_ID"
```

Azure-hosted application は、`readerPrincipalId` に割り当てられた Data Reader のまま
マネージド ID で読み取ります。
