# マルチテナント × Azure App Configuration サンプル

Azure Architecture Center の記事
[Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration)
を更新したコミット
[`bef1a19`](https://github.com/MicrosoftDocs/architecture-center/commit/bef1a19651d0ea5a9bd01ede617d225b7a39f0f5)
（[PR #16561](https://github.com/MicrosoftDocs/architecture-center/pull/16561)）の変更点を、
動くコードとテストで確かめるためのサンプルです。変更点ごとに、どこで何を確かめられるかを
次の表にまとめます。大半は Azure サブスクリプションなしで、フェイクストア上のテストで
確かめられます（[動かす](#動かす)）。

## コミット bef1a19 の変更点と確かめる場所

| 記事の節 | 変更点 | 確かめる場所 | 確かめ方 |
| --- | --- | --- | --- |
| Shared stores | 「1時間あたりの最大リクエスト数」という Standard 前提の説明を、ティアごとのストレージ・リクエストクォータ・スループット上限へ一般化。Standard は geo-replication でレプリカごとにクォータを持ち、Premium はクォータなし | [01 リクエストクォータと geo-replication](samples/01-shared-store-key-prefix/#リクエストクォータと-geo-replication)、[ティアごとの上限](#ティアごとの上限) | 文書照合 |
| Shared stores | geo-replication は noisy neighbor を防がない。テナント単位のレート制限と監視が必要 | 01 の同じ節 | 文書照合 |
| Store per tenant | ストア数が無制限なのは Standard と Premium。Developer tier は SLA がなく非本番用 | [03 コスト上の注意](samples/03-store-per-tenant/#コスト上の注意) | 文書照合 |
| Store per tenant | CMK は Standard/Premium のストア単位。異なる CMK が必要なテナントごとにストアを分ける | [03 いつ選ぶか](samples/03-store-per-tenant/#いつ選ぶか) | 文書照合（Bicep は CMK を構成しない） |
| Application-side caching | 「provider は設定をキャッシュし自動で refresh する」から「キャッシュする」へ変更 | [テナント単位にロードしてキャッシュする](#テナント単位にロードしてキャッシュする)、[refresh には明示的な契機が要る](#refresh-には明示的な契機が要る) | フェイクテスト（Python）、単体テスト（.NET） |
| Refresh key-values（新設） | sentinel key を全体共通にするかテナント別にするかを選ぶ。.NET は `ConfigureRefresh` で登録し、`TryRefreshAsync` かミドルウェアで refresh する | [05 .NET キャッシュリフレッシュ](samples/05-dotnet-cache-refresh/)、[refresh には明示的な契機が要る](#refresh-には明示的な契機が要る) | 単体テスト（.NET 実 SDK での値更新は未検証） |
| Configuration rollout and rollback with snapshot references（新設） | テナントスコープの参照キーを差し替えてロールアウト/ロールバックする。スコープは中身を絞らない。差し替え前に refresh の構成が必要。スナップショットのサイズ上限。アクセス制御はストア単位のまま | [04 スナップショット参照](samples/04-snapshot-references/) | フェイクテスト、オプトイン live テスト |
| Configuration delivery to client applications（新設、プレビュー） | Front Door でクライアントの読み取りを吸収する。匿名公開・sentinel key 不可・結果整合 | [Front Door 経由のクライアント配信は実装していない](#front-door-経由のクライアント配信は実装していない) | 文書照合のみ |

## テナント単位にロードしてキャッシュする

記事の Application-side caching 節が勧める「テナントごとに、必要になったときにロードし、
テナントごとにキャッシュする」を、Python サンプルは次のように実装しています。

- テナントの設定は最初のリクエストで遅延ロードし、全テナントを一括ロードしません。
- 外側のキャッシュは TTL と `max_entries`（LRU）で有界に保ち、expire/evict 時にそのテナント固有の
  provider を閉じます（01・04 はテナントのプレフィックス、02 はラベル、03 は専用ストアの provider）。
- 全テナントが使う共有 provider は閉じません。そのため共有設定の古さはテナント TTL では制限されません。

## 記事の補足: メモリ圧迫時の削除はアプリケーションが行う

記事の Refresh key-values 節は、テナントの `IConfiguration` を in-memory cache に置くと
"the cache can remove unused instances if your application is under memory pressure"
と書いています。この削除は、アプリケーションが仕組みを用意したときにだけ起きます。ASP.NET Core の
`IMemoryCache` は、メモリ圧迫を検知して自分からは削除しません
（[公式ドキュメント](https://learn.microsoft.com/aspnet/core/performance/caching/memory#limit-cache-size-with-setsize-size-and-sizelimit):
"The ASP.NET Core runtime doesn't trim the cache when system memory is low"）。

テナントが増えても provider がメモリを占め続けないよう、次のいずれかを設計し、eviction callback
で provider を破棄します。

- 有効期限（absolute / sliding expiration）
- `SizeLimit` と各エントリーの `Size`
- メモリが逼迫したときに `Compact` / `Remove` を呼ぶ処理

このリポジトリでは、サンプル05のキャッシュは単純な `Dictionary` で、上のどれも持ちません。
Python サンプルは[前の節](#テナント単位にロードしてキャッシュする)のとおり TTL と `max_entries` で上限を設けています。

## refresh には明示的な契機が要る

コミットは Application-side caching 節から「provider が自動で refresh する」という記述を外し、
Refresh key-values 節を新設しました。refresh はどちらの言語でもバックグラウンドだけでは進まず、
アプリケーション側の契機が必要です。

- **.NET（[05](samples/05-dotnet-cache-refresh/)）:** `ConfigureRefresh` でテナント別の sentinel key
  （`{tenantId}/Sentinel`）を登録し、`TryRefreshAsync` を明示的に呼びます。テナント別 sentinel
  なので、共有キーを変えても各テナントの sentinel を更新するまで検知されません。これが全体共通
  sentinel との trade-off です。**このサンプルだけ .NET 製で、`uv run pytest` ではなく
  `dotnet test` で実行します。**
- **Python（01〜04）:** リクエスト処理中に `provider.refresh()` を呼びます。sentinel key
  （`refresh_on`）は指定せず、provider に選択したキー全体の変更を監視させています。

## スナップショット参照でテナント単位にロールアウト/ロールバックする

[04 スナップショット参照](samples/04-snapshot-references/) は、コミットで新設された節の実装です。
01 の分離モデルはそのままに、テナントスコープの参照キーを不変スナップショットへ向け、参照先を
変えるだけでコード変更・再デプロイなしにロールアウト/ロールバックします。記事が注意する
「参照キーのスコープはスナップショットの中身を絞らない」ことは、負例のテストで示しています。

## Front Door 経由のクライアント配信は実装していない

コミットで新設されたクライアント配信の節（プレビュー、Azure パブリッククラウドのみ）は、
このリポジトリでは実装せず、文書照合だけを行っています。対象がバックエンドサーバーでテナント設定を
解決するモデルだけだからです。記事の要点は次のとおりです。

- ブラウザ・モバイル・デスクトップが設定を直接読むと、テナント数が多いほど共有ストアの
  [リクエストクォータ](samples/01-shared-store-key-prefix/#リクエストクォータと-geo-replication)
  に近づく。[Azure Front Door 経由の配信](https://learn.microsoft.com/azure/azure-app-configuration/concept-hyperscale-client-configuration)
  はエッジでキャッシュしてこの読み取りを吸収する。
- **匿名で公開アクセス可能**。秘密情報やテナント固有の非公開データを含まない専用ストアを使い、
  Front Door のルートをテナント間の認可境界として扱わない。
- [sentinel key による refresh は使えない](https://learn.microsoft.com/azure/azure-app-configuration/how-to-load-azure-front-door-configuration-provider?tabs=dotnet-maui#troubleshooting)。
  選択したすべてのキーを監視する。
- 結果整合で、Front Door のキャッシュ失効とクライアントの次回 refresh の後に反映される。
  即時反映が必要な設定には使わない。
- 採用する場合は、Front Door のマネージド ID に読み取り専用のロールだけを与える。専用ストアの
  定義・キャッシュ調整・レプリカオリジンの構成は、
  [Front Door 経由の配信の記事](https://learn.microsoft.com/azure/azure-app-configuration/concept-hyperscale-client-configuration)
  に従って別途設計する。

## ティアごとの上限

コミットは共有ストアの上限を「1時間あたりの最大リクエスト数」から、ティアごとの上限へ一般化しました。

| | Free | Developer | Standard | Premium |
| --- | --- | --- | --- | --- |
| ストア数上限 | 3 / リージョン / サブスクリプション | 上限なし（ストアごとに課金） | 上限なし（ストアごとに課金） | 上限なし（ストアごとに課金） |
| リクエスト | 1,000 / 日 | 6,000 / 時 | 30,000 / 時 | 制限なし |
| ストレージ | 10 MB | 500 MB | 1 GB | 4 GB |

出典: [Azure subscription and service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-app-configuration)

Developer tier には SLA がないため、低トラフィックの非本番用途向けです。ストア単位の SLA が
必要な本番環境では Standard または Premium を選びます。03 は共有ストアも必要なので、Free
tier で同一リージョンに作れる構成は共有1ストア + テナント専用2ストアまでです。

## 前提となる分離モデル（01〜03）

01〜03 は、記事が以前から示していた分離モデルを動かして比べるサンプルです。04・05 はこの上に
載ります。

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
使うと、バージョニングや環境の区別にラベルを使えなくなるためです。テナントごとに異なる CMK
が必要な場合、またはテナントが設定データの分離を要求する場合にだけ 03 を選びます。
**このリポジトリの Bicep は CMK を構成しません**。CMK を有効にするには、Standard または Premium
ストア、ストア自身のマネージド ID、その ID への Key Vault キー権限（RBAC なら
**Key Vault Crypto Service Encryption User**）、ストアの
[暗号化設定](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-customer-managed-keys)
を別途構成する必要があります。

## 動かす

`script/` の3つのコマンドで、01〜04 のどのサンプルもローカルのフェイクストアか Azure の実ストアで
動かせます。コマンドはリポジトリルートで実行し、`--sample` にサンプル番号を渡します。

### ローカル（フェイクストア、Azure 不要）

```bash
uv run pytest                        # 全テスト
RUN=$(script/bootstrap --sample 01)
script/server --run "$RUN"           # ポートは 5000 + サンプル番号。Ctrl-C で停止
```

- `http://localhost:5001/` — テナント一覧
- `http://localhost:5001/t/tenant-a/api/config` — 解決後の設定（JSON）
- `http://localhost:5001/_diagnostics/cache` — キャッシュの hit/miss/evict

### Azure の実ストア

```bash
az login
RUN=$(script/bootstrap --sample 01 --azure --sku developer)
script/server --run "$RUN"
# 終わったら Ctrl-C でサーバーを止めてから
script/cleanup --run "$RUN"
```

`bootstrap --azure` は次を行います。

1. 実行ごとに新しいリソースグループを作ります（既存のグループは再利用しません）。
2. サンプルの `main.bicep` をデプロイし、サインイン中のユーザーに **App Configuration Data Reader** を付与します。
3. 一時的に **Data Owner** を付与してフェイクと同じ値を投入し、投入後すぐに外します。

そのためサーバーは Data Reader だけでストアを読みます。03 の複数エンドポイントなど、
サンプルごとの環境変数は `server` がデプロイ出力から設定します。

- **SKU とリージョン:** 既定は `--sku standard`、`--location japaneast`、現在の `az account` の
  サブスクリプションです。デモなら `--sku developer` で足ります。
- **起動直後の 403 / 503:** 新しいロール割り当てがデータプレーンで有効になるまで最大約15分かかる
  ことがあります。待ってから再試行してください。
- **後片付け:** `cleanup` は、その実行で作ったタグ付きのリソースグループだけを削除します。
  実行の記録（状態・デプロイ出力）は `.runs/<run ID>/` に残ります。

`script/` を使わずに手順を追う場合は、各サンプルの README の「実ストアにデータを入れる」を
参照してください。サンプル04のオプトイン live テストは参照キーを書き換えるため、実行中は
Data Owner が必要です（手順はサンプル04の README）。

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

テナント ID の検証、ストア障害時の応答、タイムアウト、provider と資格情報の寿命といった
実装の詳細は [実装メモ](docs/implementation-notes.md) にまとめています。

## 検証状況

| 検証種別 | このリポジトリでカバーする範囲 |
| --- | --- |
| フェイク実行（既定 `uv run pytest`、301 collected） | 3パターンの設定解決一致、キャッシュのclose/expiry、スナップショット参照の正例・負例 |
| 実SDKオプトイン（`--run-live`） | sample 04 の参照解決・別テナント不変・実書換後refresh・ロールバック |
| IaCコンパイル | `main.bicep` 4本の `az bicep build` |
| 文書照合のみ | geo-replication / Developer SLA / CMK / Front Door |
| 未検証 | 読み取り拒否（RBAC負例）、.NET実SDKでの値更新 |

入力ハッシュは [`docs/reviews/2026-09-07-training-inputs.json`](docs/reviews/2026-09-07-training-inputs.json) にあります。

## 既知の制約

- Python の既定テストはフェイクストアと SDK double を使い、Azure へ接続しません。
  実ストアでの動作を確認するには `requirements-azure.txt` をインストールし、ストアと権限を
  用意してオプトインliveテストを実行してください。provider の依存指定は `>=2.5.0` です。
  実接続を確認する際は、解決された SDK バージョンも記録してください。
- スナップショット参照の解決は実運用では configuration provider(SDK)側が自動的に行います。
  `samples/04-snapshot-references/` はこの解決ロジックをフェイクストア(`src/mtappconfig/fake.py`)
  内で再現しています。実接続のアダプターは解決を SDK に任せており、参照解決を独自実装しません。
  実ストアでの参照解決やロールバックの確認手順はサンプル04を参照してください。
- サンプル05(.NET)の `dotnet restore` は、その環境の NuGet 構成にあるパッケージソースを使い、
  **既存キャッシュを前提としません**。`NuGet.Config` の階層や設定したソースを確認し、
  必要に応じて利用者またはCIの設定を用意してください。ソースの一覧はリポジトリルートで
  `dotnet nuget list source` を実行すると確認できます。設定の場所や復元手順は
  [samples/05-dotnet-cache-refresh/README.md](samples/05-dotnet-cache-refresh/) を参照してください。
