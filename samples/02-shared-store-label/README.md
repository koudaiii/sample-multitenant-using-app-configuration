# 02 — 共有ストア + ラベル

1つのストアを共有し、**ラベル**でテナントを区別します。キーにはプレフィックスを付けません。

## ストアの中身

| キー | 値 | ラベル |
| --- | --- | --- |
| `App:SupportEmail` | `support@contoso.example` | なし |
| `App:Version` | `1.4.2` | なし |
| `LogLevel` | `Warning` | `tenant-a` |
| `DatabaseName` | `db-tenant-a` | `tenant-a` |
| `LogLevel` | `Debug` | `tenant-b` |

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
