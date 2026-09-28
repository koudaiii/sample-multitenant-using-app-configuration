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

- ラベルをテナント識別に使う代償は[上記のトレードオフ](#トレードオフ)のとおりです
  （バージョニング・環境の区別に使えなくなる）。
- ラベルフィルタの指定漏れは「ラベルなし」の設定しか返らないという既定挙動により、
  気づきにくいバグになりやすい。

### 想定シナリオ

- テナントごとに別々のアプリケーションインスタンス／デプロイを持ち、各インスタンスが
  自分のラベルだけを読めばよい構成。

### アンチパターンシナリオ

- 同一アプリケーションが複数テナントを横断的に捌く構成（01 と同じユースケース）なのに、
  あえてラベルを使う。[トレードオフ](#トレードオフ)の代償だけを払うことになる。
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

[01 と同じ制約](../01-shared-store-key-prefix/#リクエストクォータと-geo-replication)がそのまま
当てはまります（共有ストア1つを全テナントで使う点は同じため）。

## 実ストアにデータを入れる

投入手順は [01 の同名節](../01-shared-store-key-prefix/#実ストアにデータを入れる)と同じです
（一時的な Data Owner 付与・RBAC反映待ち・`disableLocalAuth: true` の制約も共通）。
違うのは、テナント別キーを `--label <tenant-id>` で書き分ける点だけです。

```bash
# 共有2件はラベルなし、テナント別は --label <tenant-id> を付ける
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "App:SupportEmail" --value "support@contoso.example"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "DisplayName" --label "tenant-a" --value "Tenant A"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "DisplayName" --label "tenant-b" --value "Tenant B"
```

残りのキーも上の「ストアの中身」の表（キー・値・ラベルの列）どおりに同じ要領で投入し、
最後に `APPCONFIG_ENDPOINT` を設定してアプリを起動します。

```bash
export APPCONFIG_ENDPOINT="https://$STORE.azconfig.io"
uv pip install -r ../../requirements-azure.txt
uv run flask --app app run --port 5002
```

テスト終了後は [01 と同じ手順](../01-shared-store-key-prefix/#実ストアにデータを入れる)で、
一時的な Data Owner 割り当てを削除してください。
