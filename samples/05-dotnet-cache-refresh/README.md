# 05 — .NET: 明示的なキャッシュリフレッシュ

**このサンプルだけ .NET(C#)製です。** 01〜04(Python)とはビルド・テストの系列が完全に
独立しています。`uv run pytest` の対象ではなく、`dotnet test` で実行します。

対象記事: [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration) ―
「Application-side caching」節の .NET 固有の記述
(`ConfigureRefresh` で登録し、`TryRefreshAsync` またはミドルウェアでリフレッシュを
トリガーし、テナントの `IConfiguration` オブジェクトをテナント ID をキーにキャッシュする)

## 構成

| ファイル | 役割 |
| --- | --- |
| `ITenantConfigRefresher.cs` | `IConfigurationRefresher.TryRefreshAsync` を薄くラップする自前の抽象化。テストは常にこちらを介する。 |
| `TenantConfigurationCache.cs` | テナント ID をキーに `IConfiguration` をキャッシュし、明示的な `RefreshAsync` を提供する中核ロジック。**xUnit でテスト対象。** |
| `AzureConfigurationRefresher.cs` | 実際に `AddAzureAppConfiguration` + `ConfigureRefresh` + `GetRefresher()` を配線する1ファイル。実 SDK に対してコンパイルし、不正 ID の拒否は実行検証するが、実ストアへの接続はしない。 |
| `TenantId.cs` | 公開キャッシュ/loader境界で共通利用する、テナント ID の構文検証。登録確認・認可は呼び出し側の責務。 |
| `Program.cs` | 最小コンソールアプリ(実行には実ストアの接続情報が必要)。 |
| `Tests/TenantConfigurationCacheTests.cs` | キャッシュ・refresh・キャンセル・テナント ID の入力境界を検証。 |
| `Tests/ProgramTests.cs` | 不正な endpoint が通信前に actionable なメッセージと終了コード1になることを検証。 |

## 中核のコード

```csharp
// AzureConfigurationRefresher.cs
options.Select($"{SharedPrefix}*");
options.Select($"{tenantId}/*");
options.TrimKeyPrefix(SharedPrefix);
options.TrimKeyPrefix($"{tenantId}/");
```

サンプル01の `KeyPrefixSource`(`select(key_filter=..., trim_prefixes=...)`)とキーの
選択条件(フィルタ)は構造的に同一です — `Select` が `key_filter`、`TrimKeyPrefix` が
`trim_prefixes` に対応します。ただし、サンプル01は共有設定とテナント設定を
`{**shared, **tenant}` で明示的にマージし、テナント側が常に勝つことをテストで
保証しています。このサンプルは2回の `Select` を同じプロバイダーに投入し、
`TrimKeyPrefix` 適用後に同名キーとなった場合の優先順位はプロバイダー内部の解決に
委ねています。この優先順位はMicrosoftのドキュメントに明記されておらず、本サンプルでは
検証していません。

```csharp
// TenantConfigurationCache.cs
public async Task<bool> RefreshAsync(string tenantId, CancellationToken cancellationToken = default)
{
    // ...
    return await entry.Refresher.TryRefreshAsync(cancellationToken);
}
```

記事が言う「`TryRefreshAsync` を呼んでリフレッシュを明示的にトリガーする」を、
テナント単位で行います。

## ミドルウェアによるリクエスト駆動のリフレッシュ(実装なし)

記事はもう1つの経路として ASP.NET Core ミドルウェアを挙げています。

```csharp
app.UseAzureAppConfiguration();
```

これはホストの構成を `AddAzureAppConfiguration` で構成し、
`builder.Services.AddAzureAppConfiguration()` で関連サービスを登録した場合の経路です。
ミドルウェアは DI の `IConfigurationRefresherProvider` が公開する provider に対して、
リクエストごとに refresh を確認します。
これはバックグラウンドタイマーではなく、リクエストというアプリケーション活動を
契機にした確認です。

**このサンプルのテナント設定は、それぞれ別の `ConfigurationBuilder` で構築した
`IConfiguration` root です。** キャッシュ内に独立して保存した root を、このミドルウェアが
自動的に発見するわけではありません。上の1行だけでテナントキャッシュの明示的な呼び出しを
置き換えることはできません。このキャッシュを Web アプリへ組み込むなら、認証・テナント解決・
認可の後に、対象テナントの `cache.RefreshAsync(tenantId, context.RequestAborted)` を呼ぶ
テナント対応の処理を別途配線してください。全テナントを毎リクエスト refresh する必要はありません。
このサンプルはフルの Web アプリを追加するとスコープが大きくなりすぎるため、
概念とコード片の紹介に留め、実装はしていません。

## 公開 API の入力境界とキャンセル

`Get`、`RefreshAsync`、`AzureConfigurationRefresher.Load` はテナント ID の構文を検証します。
Python と同じく3〜32文字の小文字 ASCII 英数字とハイフンのみを許可し、先頭・末尾は英数字に
限定します。`*`、カンマ、スラッシュ、末尾の改行などを selector 構築前に拒否します。
これは**登録確認でも認可でもありません**。Program は既知の `tenant-a` / `tenant-b` だけを
渡しますが、Web 化する際は呼び出し元の認証済みコンテキストからテナントを解決し、その ID の
登録・アクセス権を確認してから公開 API へ渡してください。

未キャッシュの `RefreshAsync` は、ロック取得後、loader を呼ぶ直前にキャンセルを確認し、
要求済みなら `OperationCanceledException` を返します。同期 `Build()` が始まった後は
この token で中断できません。キャッシュ済みの場合は従来どおり token を refresher に渡し、
SDK が処理するキャンセルの `false` も含め、refresher の結果をそのまま返します。
Program の空・不正・HTTPS 以外の `APPCONFIG_ENDPOINT` は通信前に終了コード1で拒否し、
正しい HTTPS URL の設定方法を標準エラーへ表示します。

## いつ選ぶか

.NET でこのリポジトリの分離パターン(01〜04)を実装する場合に、テナントごとの
`IConfiguration` をどうキャッシュし、いつリフレッシュを確認するかという設計判断の
参考として。

## メリット・デメリットとアンチパターン

### メリット

- `TryRefreshAsync` はリフレッシュ間隔が経過するまでは何もせず高速に返り(no-op も
  成功扱い)、SDKが処理する通信・認証・Key Vault・キャンセル・形式エラーでは
  `false` を返してキャッシュ済みの値を使い続ける。このため、キャッシュされた
  テナントに対して安全に頻繁な呼び出しができる。想定外の例外まで常に握りつぶす
  契約ではないため、呼び出し側の通常の例外処理は別途必要。
- テナントごとに独立した `IConfigurationRefresher` を持つため、あるテナントの
  リフレッシュ失敗が他のテナントに影響しない。
- ホスト/DIに登録した provider は標準ミドルウェアでリクエスト駆動にできる。ただし、
  本サンプルの独立したテナント root は別途テナント対応の refresh 配線が必要。

### デメリット

- 実行には必ず実ストアへの接続が必要で、Python サンプルのようなインメモリ
  フェイクによるオフライン実行ができない(下記「既知の制約」参照)。
- テナント数だけproviderインスタンスと `IConfigurationRefresher` のrefresh状態を持つ
  ことになり、テナント数が多い場合は接続リソース・メモリ使用量に注意が必要。
- センチネルキーの登録を忘れると、`ConfigureRefresh` で登録した意図に反して
  リフレッシュが働かない(Register の引数を間違えるとテナント間で意図しない
  キーを監視してしまうこともある)。
- `refreshAll: true` は「このプロバイダーインスタンスが使う全キー」をリフレッシュ
  する(テナント自身のプレフィックスだけでなく `_shared/*` も含む)。しかし
  テナントごとに別々の `IConfigurationRefresher` インスタンスを持つため、共有キーを
  変更しても、そのテナント自身のセンチネルキーを更新しない限り検知されない。
  サンプル01が言う「1つの値、1つの更新箇所」という共有設定の利点が、このサンプルでは
  「1つの値、テナントの数だけセンチネルを更新」に変わる点に注意。
- このキャッシュには TTL も LRU もなく、一度ロードしたテナントの
  `IConfigurationRefresher` は解放されません。テナント数が多い長時間稼働の
  プロセスでは、provider状態と関連リソースが増え続けます。
  [`IMemoryCache`](https://learn.microsoft.com/aspnet/core/performance/caching/memory#use-setsize-size-and-sizelimit-to-limit-cache-size)
  に置き換えるだけでもメモリ圧迫時の自動回収は保証されません。本番では `SizeLimit` と
  各エントリーの `Size` を設定し、eviction callbackなどでproviderを破棄してください。
  現在のPythonサンプルは `max_entries` とTTLを明示し、expire/evict時にテナント固有
  providerを閉じます(共有providerはテナントTTLの対象外です)。
- `Get` のcold loadは全テナント共通のロック内で行います。同じテナントへの同時loadを
  1回にまとめる代わりに、遅いテナントのload中は別テナントの `Get` も待ちます。本番で
  テナント間の待ち時間まで分離するには、テナント単位のロックなどを検討してください。

### 想定シナリオ

- .NET でこのリポジトリの分離パターンを実装し、テナントごとに設定の鮮度を
  制御したい場合。

### アンチパターンシナリオ

- `TryRefreshAsync` を一度も呼ばず、`ConfigureRefresh` の登録だけして満足する。
  ミドルウェアを使わない構成では、明示的に呼ばない限りリフレッシュは発生しない。
- 全テナント共通で1つの `IConfigurationRefresher` を使い回そうとする。
  テナントごとに異なるキーフィルタ・センチネルキーを扱うには、テナントごとに
  別々の `IConfigurationRefresher`(＝別々の `IConfigurationBuilder`)が必要。

## 動かす

### NuGet パッケージの復元

`dotnet restore` は NuGet の通常の構成探索でパッケージソースを決め、**既存キャッシュを前提としません**。
この開発環境では、承認されたローカルの `NuGet.config` に定義した `azure-default` プロキシから、
空のパッケージディレクトリへの復元を確認しています。社内用の設定ファイルは **Git 管理しません**。
利用する場合は、リポジトリルートへローカルまたはCIで別途配置してください。プロキシ設定が
このリポジトリへコミットされている前提ではありません。その他の環境では、その環境の到達可能な
NuGetソースを使用します。

次のコマンドはリポジトリルートから実行します。ソース指定の上書きは不要です。

```bash
cd samples/05-dotnet-cache-refresh/Tests
dotnet restore
dotnet test --no-restore
```

実行(`Program.cs`)には実ストアが必要です。

```bash
export APPCONFIG_ENDPOINT=https://<your-store>.azconfig.io
cd samples/05-dotnet-cache-refresh
dotnet run
```

起動時にtenant-a/bの初期値を表示します。ストアの値と各テナントのセンチネルを更新し、
30秒以上待ってからEnterを押すと、同じプロセス内のキャッシュに対して
`RefreshAsync` を呼び、リフレッシュ後の値を表示します。

## Azure での実行(RBAC とストアのレイアウト)

`Connect(Uri, DefaultAzureCredential)` が必要とするロールは、他のサンプルと同じく
**App Configuration Data Reader** だけです。これはアプリ実行時の読み取り権限であり、
下記の `az appconfig kv set` で初期データやセンチネルを書き込む操作者には、一時的な
**App Configuration Data Owner** または同等のデータプレーン書き込み権限が別途必要です。

このサンプルはサンプル01と同じキーレイアウト(`_shared/*` と `{tenantId}/*`)を読むため、
[サンプル01用に投入済みのストア](../01-shared-store-key-prefix/#実ストアにデータを入れる)を
そのまま `APPCONFIG_ENDPOINT` に指定して使えます。

ただし、このサンプルはリフレッシュ検知のために**センチネルキー**を追加で必要とします。
最低限、次のキーを投入してください。

```bash
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/Sentinel" --value "1"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-b/Sentinel" --value "1"
```

`RefreshAsync` を呼んだときに実際のリフレッシュを発生させるには、リフレッシュ間隔
(30秒)が経過したあとに、このセンチネルキーの値を変更してください
(`az appconfig kv set` で値を変えるだけで十分です)。

この手順は、この開発環境が実ストアに接続できないため実行検証していません
(下記「既知の制約」参照)。

## 既知の制約

- **フェイクストアがありません。** Python サンプル(01〜04)は Azure サブスクリプション
  なしでも `uv run pytest` や `flask run` が動きますが、このサンプルは実 Azure SDK
  (`Microsoft.Extensions.Configuration.AzureAppConfiguration`)を直接使うため、
  `AzureConfigurationRefresher.Load` を呼ぶと必ず実ストアへの接続を試みます。
  テストはキャッシュと入力ガード（loader の不正 tenant 拒否、Program の不正 endpoint 拒否）
  をオフラインで検証します。正しい入力からの実 SDK load はコンパイル確認のみで、通信・RBAC・
  実ストアでの refresh は未検証です。
- 未取得のパッケージを復元するには、構成した NuGet ソースへの通信が必要です。
  NuGet プロキシからの復元成功は、実 App Configuration ストアへの接続や RBAC の検証を
  意味しません。復元後の単体テストは Azure 接続なしで実行できます。
