# マルチテナント × Azure App Configuration サンプル

[Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration)
が示す分離モデルを、動かして違いが分かる Flask サンプル3本にしたものです。
評価軸は [Azure Well-Architected Framework](https://learn.microsoft.com/en-us/azure/well-architected/)。

## 3つのパターン

| | [01 キープレフィックス](samples/01-shared-store-key-prefix/) | [02 ラベル](samples/02-shared-store-label/) | [03 テナント別ストア](samples/03-store-per-tenant/) |
| --- | --- | --- | --- |
| ストア数 | 共有1つ | 共有1つ | テナント数だけ + 共有1つ |
| 分け方 | キー `tenant-a/LogLevel` | ラベル `tenant-a` | ストアそのもの |
| データ分離 | 低 | 低 | 高 |
| 性能分離 | 低 | 低 | 高 |
| デプロイ/運用の複雑さ | 低 | 低 | 中〜高 |
| コスト | 低 | 低 | 中〜高 |
| ラベルの空き | 環境・バージョンに使える | テナントで占有 | 環境・バージョンに使える |

**迷ったら 01。** 記事も既定としてキープレフィックスを推奨しています。ラベルをテナント識別に
使うと、バージョニングや環境の区別にラベルを使えなくなるためです。テナントごとに顧客管理キー
(CMK) が必要、またはテナントが設定データの分離を要求する場合にだけ 03 を選びます。

## 動かす

Azure のサブスクリプションは不要です。既定ではメモリ上のフェイクストアが使われます。

```bash
uv sync
uv run pytest                                   # 全テスト
cd samples/01-shared-store-key-prefix
uv run flask --app app run --port 5001
```

- `http://localhost:5001/` — テナント一覧
- `http://localhost:5001/t/tenant-a/` — 解決後の設定
- `http://localhost:5001/_diagnostics/cache` — キャッシュの hit/miss/evict

実際の App Configuration に繋ぐ場合は `main.bicep` でストアを作り、環境変数を設定します。

```bash
uv pip install -r requirements-azure.txt
export APPCONFIG_ENDPOINT=https://<your-store>.azconfig.io
uv run flask --app app run --port 5001
```

Azure SDK を `pyproject.toml` ではなく `requirements-azure.txt` に置いているのは意図的です。
`uv lock` は optional-dependencies も解決対象に含めるため、そこに書くと「フェイクだけで
動かしたい人」の `uv sync` まで巻き込んで失敗します。

認証は `DefaultAzureCredential` です。実行する ID に **App Configuration Data Reader**
ロールを付与してください（`main.bicep` の `readerPrincipalId` で割り当てられます）。

## 構成

パターン間の差分は各サンプルの `source_*.py` に集約してあります。共通部分
（テナント ID 検証・キャッシュ・Flask のルート）は `src/mtappconfig/` に1回だけ書かれています。

```bash
diff samples/01-shared-store-key-prefix/source_key_prefix.py \
     samples/02-shared-store-label/source_label.py
```

`tests/test_pattern_contract.py` が、**3パターンとも同じ解決結果を返す**ことを検証しています。
だからこの diff がパターンの違いのすべてです。

## Well-Architected の観点

### セキュリティ

- 接続文字列・アクセスキーを使いません。`DefaultAzureCredential` のみで、Bicep は
  `disableLocalAuth: true` を設定します。
- 権限は **App Configuration Data Reader**（`516239f1-63e1-4d78-a4de-a74fb236a071`）だけ。
- **テナント ID の検証がこのサンプルで最も重要な部分です。** URL 由来のテナント ID を検証せずに
  キーフィルタへ渡すと、`*` を指定するだけで全テナントの設定が読めてしまいます。
  `src/mtappconfig/tenants.py` のレジストリ照合と正規表現を通った ID だけがストアに到達します。
- 機密値は App Configuration ではなく Key Vault に置き、Key Vault 参照として保存してください。

### 信頼性

- 設定ストアが落ちてもキャッシュ済みの値で応答を続けます（`src/mtappconfig/cache.py`）。
- `/healthz` は依存先を叩かず、`/readyz` はストア到達性を含みます。

### パフォーマンス効率

- テナント単位の遅延ロード。全テナント一括ロードはしません。
- TTL + LRU でメモリを有界に保ちます。
- リクエスト契機のリフレッシュで、毎リクエストではストアを叩きません。

### コスト最適化

| | Free | Developer | Standard | Premium |
| --- | --- | --- | --- | --- |
| ストア数 | 3 / リージョン / サブスクリプション | 無制限 | 無制限 | 無制限 |
| リクエスト | 1,000 / 日 | 6,000 / 時 | 30,000 / 時 | 制限なし |
| ストレージ | 10 MB | 500 MB | 1 GB | 4 GB |

出典: [Azure subscription and service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-app-configuration)

**Free tier ではストアが1リージョンあたり3つまで**なので、パターン03 は Free では実質
3テナントまでです。共有ストア方式でも、テナント数が増えれば1ストアのリクエスト/時と
ストレージの上限に達しうるため、その場合は複数の共有ストアにテナントを分散します。

## 既知の制約

- このリポジトリは PyPI のファイル配信ホスト (`files.pythonhosted.org`) に到達できない
  環境で開発されました。同様の環境では uv コマンドに `--offline` を付けてください
  （`uv sync --offline`、`uv run --offline pytest`）。通常のネットワーク環境では不要です。
- `src/mtappconfig/azure_source.py`（実 App Configuration への接続）は、開発環境から
  `azure-appconfiguration-provider` を取得できないため**実行検証されていません**。
  この1ファイルだけが未検証で、それ以外はフェイクストアに対して全テストが通ります。
  検証時は次の2点を最初に確認してください。
  - `SettingSelector(label_filter=...)` で「ラベルなし」を表す値（本実装は `"\0"`）
  - `load(startup_timeout=...)` の引数名
