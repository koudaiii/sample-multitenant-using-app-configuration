# 01 — 共有ストア + キープレフィックス

1つの App Configuration ストアを全テナントで共有し、キーの先頭にテナント ID を付けて
区別します。**記事が既定として推奨するパターン**です。

## ストアの中身

| キー | 値 | ラベル |
| --- | --- | --- |
| `_shared/App:SupportEmail` | `support@contoso.example` | なし |
| `_shared/App:Version` | `1.4.2` | なし |
| `tenant-a/LogLevel` | `Warning` | なし |
| `tenant-a/DatabaseName` | `db-tenant-a` | なし |
| `tenant-b/LogLevel` | `Debug` | なし |

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
