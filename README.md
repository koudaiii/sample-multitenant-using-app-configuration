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
(CMK) の境界が必要、またはテナントが設定データの分離を要求する場合にだけ 03 を選びます。
ただし、03 はストアを分けて CMK の境界を作れるだけで、**このリポジトリの Bicep は CMK を
構成しません**。CMK を有効にするには、Standard または Premium ストア、ストア自身の
マネージド ID、その ID への Key Vault キー権限（RBAC なら
**Key Vault Crypto Service Encryption User**）、ストアの
[暗号化設定](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-customer-managed-keys)
を別途構成する必要があります。

## クライアント直接配信(Azure Front Door、プレビュー)

ブラウザ・モバイル・デスクトップなど、バックエンドを介さずに設定を直接読むクライアントを持つ
場合、テナント数が多いと共有ストアの[リクエストクォータ](samples/01-shared-store-key-prefix/#リクエストクォータと-geo-replication)
に近づくことがあります。[Azure Front Door 経由のクライアント設定配信](https://learn.microsoft.com/azure/azure-app-configuration/concept-hyperscale-client-configuration)
(プレビュー、Azure パブリッククラウドのみ)はエッジで設定をキャッシュし、この読み取り量を吸収します。

この配信経路は**匿名で公開アクセス可能**です。秘密情報やテナント固有の非公開データを含まない
**専用のストア**を用意し、Front Door には読み取り専用のロールだけを与えてください。また
[sentinel key によるリフレッシュは使えません](https://learn.microsoft.com/azure/azure-app-configuration/how-to-load-azure-front-door-configuration-provider?tabs=dotnet-maui#troubleshooting) —
選択したすべてのキーを監視するようプロバイダーを設定する必要があります。この配信は結果整合的で、
Front Door 側のキャッシュ失効とクライアント側の次回リフレッシュが揃うまで更新が反映されません。
設定変更を即座に反映する必要がある用途には使わないでください。

このリポジトリはバックエンドサーバーがテナント設定を解決するモデル(01〜03)のみを対象としており、
クライアントが直接設定を読むこのパターンは実装・検証していません。Front Door 自体もローカルで
再現できないため、ここでは記事が示す注意点の要約に留めます。実装する場合は
[記事本文](https://learn.microsoft.com/azure/azure-app-configuration/concept-hyperscale-client-configuration)
に従い、専用ストアの Bicep 定義・Front Door のマネージド ID 読み取りロール・キャッシュ調整・
レプリカオリジンの構成を別途検討してください。

## ロールアウト制御(01の上に築く追加機能)

[04 スナップショット参照](samples/04-snapshot-references/) は、01〜03のような分離モデルの
選択とは別軸の追加機能です。テナント・テナントコホート・デプロイスタンプが独立したスケジュールで
設定をロールアウト/ロールバックする必要があるとき、テナントスコープの参照キーを不変スナップショット
へ向け、参照先を変えるだけでコード変更・再デプロイなしに切り替えられることを検証します。

## .NET: 明示的なキャッシュリフレッシュ

[05 .NET キャッシュリフレッシュ](samples/05-dotnet-cache-refresh/) は、記事の
Application-side caching 節が挙げる .NET 固有の記述(`ConfigureRefresh` で登録し
`TryRefreshAsync` またはミドルウェアでリフレッシュをトリガーする)を、実際にビルド・
テストできる C# コードで検証します。**このサンプルだけ .NET 製で、01〜04(Python)とは
ビルド・テストの系列が独立しています**(`dotnet test` で実行し、`uv run pytest` の
対象ではありません)。

サンプル05のキャッシュは単純な `Dictionary` であり、容量上限・TTL・破棄処理を実装して
いません。[`IMemoryCache`](https://learn.microsoft.com/aspnet/core/performance/caching/memory#use-setsize-size-and-sizelimit-to-limit-cache-size)
に置き換えてもメモリ圧迫時に自動でサイズ制限されるわけではなく、
本番では `SizeLimit` と各エントリーの `Size`、eviction時のprovider破棄を明示的に設計する
必要があります。現在のPythonサンプルは `max_entries` とTTLでテナントキャッシュを制限し、
expire/evict時にテナント固有providerを閉じます(共有providerはテナントTTLの対象外です)。
リフレッシュは両言語ともバックグラウンドだけでは進まず、.NETは `TryRefreshAsync` または
ミドルウェア、Pythonはリクエスト処理中の `provider.refresh()` という明示的な活動契機が必要です。

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
次のコマンドは、上の手順どおり `samples/01-shared-store-key-prefix` に移動した後に実行します。

```bash
uv pip install -r ../../requirements-azure.txt
export APPCONFIG_ENDPOINT=https://<your-store>.azconfig.io
uv run flask --app app run --port 5001
```

Azure SDK を `pyproject.toml` ではなく `requirements-azure.txt` に置いているのは意図的です。
`uv lock` は optional-dependencies も解決対象に含めるため、そこに書くと「フェイクだけで
動かしたい人」の `uv sync` まで巻き込んで失敗します。

認証は `DefaultAzureCredential` ですが、ローカル開発と Azure 上の実行では使う ID が異なります。

- **ローカル開発:** `az login` でサインインした開発者の資格情報を使います。各サンプルの手順では、
  データ投入とその後のローカル読み取りのため、その開発者に一時的な
  **App Configuration Data Owner** を付与し、テスト後に割り当て ID を指定して削除します。
- **Azure-hosted execution:** ホストするアプリにマネージド ID を設定し、そのオブジェクト ID を
  `main.bicep` の `readerPrincipalId` に渡します。Bicep はその ID に
  **App Configuration Data Reader** を付与し、Azure 上の `DefaultAzureCredential` は
  ホストのマネージド ID を使います。このリポジトリはアプリのホスティング自体は構成しません。

新しい RBAC 割り当てが App Configuration のデータプレーンで有効になるまで最大約15分かかる
ことがあります。直後の投入または読み取りが `403` になった場合は、待ってから再試行してください。

`APPCONFIG_ENDPOINT` は 01・02・04（共有ストア1つ）用です。**03 はストアが複数あるため
`APPCONFIG_SHARED_ENDPOINT` と `APPCONFIG_ENDPOINTS`（JSON）という別の環境変数**を使います。
詳細は [samples/03-store-per-tenant/README.md](samples/03-store-per-tenant/) を参照してください。

Bicep が付与するのは読み取り専用の Data Reader だけで、`disableLocalAuth: true` のため
接続文字列も使えません。アプリ本体とシードコードは実ストアへ書き込みません。各サンプルの README に、
そのパターンのレイアウトへ `az appconfig kv set` で投入する手順を載せています（ローカル手順では
自分の Entra ID に一時的な **App Configuration Data Owner** を別途付与します）。ただし、
**サンプル04のオプトインliveテストは読み取り専用ではありません**。ロールバックと復帰を検証するため
テスト内から `az appconfig kv set` を実行して参照キーを書き換えるので、実行中はその一時的な
Data Owner（または同等のデータ書き込み権限）を残す必要があります。手順を踏まずにデプロイだけ
済ませると、エラーなくストアが空のまま動いてしまうので注意してください。このローカルliveテストが
確認するのは、`DefaultAzureCredential` が選んだ active な資格情報で読み書きできることです。
選択された資格情報や実効ロールは検査しないため、成功しても Data Reader のみでの読み取りを
証明しません。Data Reader-only のホスト/別 ID による読み取り確認は未検証で、サンプル04の
README に分離実行手順を記載しています。

## 構成

ストアから値を選択・合成するロジックの差分は、各サンプルの `source_*.py` で比較できます。
共通部分（テナント ID 検証・キャッシュ・Flask のルート）は `src/mtappconfig/` に1回だけ
書かれています。ただし、エンドポイント環境変数やストア生成を扱う `app.py`、作成するストア数や
RBAC を定義する `main.bicep` など、実ストアへ接続する運用上の配線もパターンごとに異なります。

```bash
diff samples/01-shared-store-key-prefix/source_key_prefix.py \
     samples/02-shared-store-label/source_label.py
```

`tests/test_pattern_contract.py` が、**3パターンとも同じ解決結果を返す**ことを検証しています。
上の diff は値の選択方法を比較するためのもので、パターン間の差分すべてを示すものではありません。
`samples/04-snapshot-references/source_snapshot_references.py`
はこの比較の対象に意図的に含めていません(04は分離モデルの4つ目ではなく、01の上に築く
追加機能のため)。

## Well-Architected の観点

### セキュリティ

- 接続文字列・アクセスキーを使いません。`DefaultAzureCredential` のみで、Bicep は
  `disableLocalAuth: true` を設定します。
- Bicep が Azure-hosted application に付与する権限は
  **App Configuration Data Reader**（`516239f1-63e1-4d78-a4de-a74fb236a071`）だけです。
  ローカル手順の Data Owner はサインインした開発者への一時的な別割り当てで、テスト後に削除します。
- **テナント ID の検証がこのサンプルで最も重要な部分です。** URL 由来のテナント ID を検証せずに
  キーフィルタへ渡すと、`*` を指定するだけで全テナントの設定が読めてしまいます。
  `src/mtappconfig/tenants.py` のレジストリ照合と正規表現を通った ID だけがストアに到達します。
  ただし、これは不正な形式や未登録 ID を拒否する入力検証であり、**認可ではありません**。
  本番では URL で呼び出し元が選んだ値をそのまま信頼せず、認証済みのユーザーまたはワークロードの
  コンテキストからテナントを導出し、そのテナントへのアクセスを認可してからレジストリを解決します。
- Key Vault 参照の解決はこのサンプルでは実装していません。採用する場合は、App Configuration
  用とは別に Key Vault を読める資格情報または resolver をプロバイダーへ設定し、その ID に
  Key Vault の適切なデータプレーン RBAC を付与してください。

### 信頼性

- **すでにキャッシュ済みのテナントは**、設定ストアが落ちても直近の値で応答を続けます
  （`src/mtappconfig/cache.py` のリフレッシュ失敗はログに記録して握りつぶすだけです）。
  ただしテナントキャッシュの TTL が切れると、そのテナントが所有する Azure SDK provider を
  `close()` して query cache から削除し、次の読み込みで新しい provider と SDK `load()` を
  必ず作ります。恒久的に停止したストアではこの fresh load が失敗するため、R2 の古さの上限は
  独立したタイマーではなくこの close/recreate によって与えられます。当初検討した
  `max_staleness_seconds` は採用していません。実 SDK の `refresh()` は間隔未経過時に
  callback なしの no-op になり得るため、refresh 成功時刻を正しく判定できない設計だったためです。
  **まだキャッシュされていないテナント**（起動直後の初回リクエスト、または TTL 切れ直後に
  ストアが落ちている場合）はキャッシュに頼る値がないため `503 Service Unavailable` を
  `Retry-After` ヘッダ付きで返します。パターン03 では、あるテナント専用ストアが落ちていても
  `/readyz` は共有ストアの到達性だけを見て `ready` を返し続けます（意図的な設計です。
  1テナントの障害で他の全テナントをロードバランサから外さないため）。データとストアの障害範囲は
  そのテナントに限定されますが、**リクエスト処理まで完全には分離されません**。このサンプルの
  キャッシュは全テナント共通のロックをロード中も保持し、TTL 失効後の fresh load を含む
  実プロバイダーの新規ロードは最大100秒待つため、障害テナントのロード中は他テナントの
  リクエストも一時的に待たされ得ます。
  本番ではテナント単位のロックなどでこの待ち合わせも分離してください。
- 503 応答の `detail` フィールドは常に汎用的な文言です。実際のエラー内容（ストアの
  エンドポイントや `DefaultAzureCredential` の失敗理由など）はサーバー側のログにだけ
  出力し、未認証で到達できるエンドポイントに内部情報を漏らしません。
- `/healthz` は依存先を叩かず、`/readyz` はストア到達性を含みます。

### パフォーマンス効率

- テナント単位の遅延ロード。全テナント一括ロードはしません。
- TTL + LRU で外側キャッシュを有界に保ち、expire/evict 時には R1 対応として
  そのテナント固有の provider も閉じます。sample 01 と 04 は tenant prefix、02 は
  tenant label、03 は tenant 専用 store の exact provider だけを閉じ、全テナントが使う
  shared provider は閉じません。そのため shared provider の古さはテナント TTL では
  制限されない trade-off が残ります。
- リクエスト契機のリフレッシュで、毎リクエストではストアを叩きません。

### コスト最適化

| | Free | Developer | Standard | Premium |
| --- | --- | --- | --- | --- |
| ストア数上限 | 3 / リージョン / サブスクリプション | 上限なし（ストアごとに課金） | 上限なし（ストアごとに課金） | 上限なし（ストアごとに課金） |
| リクエスト | 1,000 / 日 | 6,000 / 時 | 30,000 / 時 | 制限なし |
| ストレージ | 10 MB | 500 MB | 1 GB | 4 GB |

出典: [Azure subscription and service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-app-configuration)

Developer tier には SLA がないため、低トラフィックの非本番用途向けです。ストア単位の SLA が
必要な本番環境では Standard または Premium を選びます。03 は共有ストアも必要なので、Free
tier で同一リージョンに作れる構成は共有1ストア + テナント専用2ストアまでです。

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
- スナップショット参照の解決は実運用では configuration provider(SDK)側が自動的に行います。
  `samples/04-snapshot-references/` はこの解決ロジックをフェイクストア(`src/mtappconfig/fake.py`)
  内だけで再現しており、`src/mtappconfig/azure_source.py` には変更を加えていません。この未検証性は
  上記の `azure_source.py` 全体の制約に準じます。
- サンプル05(.NET)は、この開発環境が `nuget.org` に到達できなかったため、過去の作業で
  展開済みだったローカルの NuGet キャッシュ(`~/.nuget/packages`)に対して
  `dotnet restore --source` を使って検証しました。通常のネットワーク環境ではこの
  オプションなしで動きます。詳細は [samples/05-dotnet-cache-refresh/README.md](samples/05-dotnet-cache-refresh/) を参照してください。
