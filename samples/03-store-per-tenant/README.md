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

**Free tier ではストアが1リージョン・1サブスクリプションあたり3つまで**です。このパターンは
共有ストアも1つ使うので、Free では2テナントまでしか作れません。Standard 以上ならストア数は
無制限です。ストアが増えるぶんデプロイと運用の対象も増えます。

なお `main.bicep` はこの上限を検査しません。Free tier で3テナント以上を指定すると、
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
