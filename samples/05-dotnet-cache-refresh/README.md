# 05 — .NET: 明示的なキャッシュリフレッシュ

**このサンプルだけ .NET(C#)製です。** 01〜04(Python)とはビルド・テストの系列が完全に
独立しています。`uv run pytest` の対象ではなく、`dotnet test` で実行します。

対象: コミット [`bef1a19`](https://github.com/MicrosoftDocs/architecture-center/commit/bef1a19651d0ea5a9bd01ede617d225b7a39f0f5)
が [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration)
に新設した「Refresh key-values」節の .NET 固有の記述
(`ConfigureRefresh` で登録し、`TryRefreshAsync` またはミドルウェアでリフレッシュを
トリガーし、テナントの `IConfiguration` オブジェクトをテナント ID をキーにキャッシュする)。
同じ段落の「メモリ圧迫時に未使用インスタンスを削除できる」は、アプリケーションが削除の仕組みを
用意した場合の話です([ルート README](../../README.md#記事の補足-メモリ圧迫時の削除はアプリケーションが行う)参照)。

## 構成

| ファイル | 役割 |
| --- | --- |
| `ITenantConfigRefresher.cs` | `IConfigurationRefresher.TryRefreshAsync` を薄くラップする自前の抽象化。テストは常にこちらを介する。 |
| `TenantConfigurationCache.cs` | テナント ID をキーに `IConfiguration` をキャッシュし、明示的な `RefreshAsync` を提供する中核ロジック。**xUnit でテスト対象。** |
| `AzureConfigurationRefresher.cs` | テナント ID を検証し、`AddAzureAppConfiguration` + `ConfigureRefresh` + `GetRefresher()` を実ストアへ接続するために配線する。 |
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
`merge_config_values(shared, tenant)` で明示的にマージし、テナント側を優先します。
このサンプルは2回の `Select` を同じプロバイダーに投入し、
`TrimKeyPrefix` 適用後に同名キーとなった場合の優先順位はプロバイダー内部の解決に
委ねています。特定の優先順位が必要な場合は、利用する SDK の動作を実ストアで確認するか、
共有設定とテナント設定をアプリケーションで明示的に合成してください。

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

記事はもう1つの経路として ASP.NET Core ミドルウェア(`app.UseAzureAppConfiguration()`)を
挙げていますが、**このサンプルには実装していません**。実装する場合は `AddAzureAppConfiguration`
でホストを構成し `UseAzureAppConfiguration()` を配置します。ミドルウェアは DI の
`IConfigurationRefresherProvider` が公開する provider に対し、リクエストが来るたびに
refresh を確認します(バックグラウンドタイマーではなく、idle 中は更新されません)。

このサンプルのテナント設定は、それぞれ別の `ConfigurationBuilder` で構築した独立した
`IConfiguration` root です。ミドルウェアはこれを自動発見しないため、Web アプリへ組み込む
なら認証・テナント解決・認可のあとに `cache.RefreshAsync(tenantId, context.RequestAborted)`
を呼ぶテナント対応の処理を別途配線してください。

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
  に置き換えても、メモリ圧迫で自動的には削除されません。本番では有効期限か `SizeLimit` と
  各エントリーの `Size` を設定し、eviction callbackでproviderを破棄してください。
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

.NET 10 SDK を用意してください。単体テストには Azure サブスクリプションは不要です。

### NuGet パッケージの復元

- `dotnet restore` は NuGet の通常の構成探索でソースを決め、**既存キャッシュを前提としません**。
- 有効なソースは、リポジトリルートで `dotnet nuget list source` を実行して確認します。
- 必要なソースやプロキシは利用者・CI 側の `NuGet.Config` で用意してください
  ([NuGet の共通構成](https://learn.microsoft.com/en-us/nuget/consume-packages/configuring-nuget-behavior))。
  資格情報や環境固有の設定はリポジトリへコミットしないでください。

```bash
dotnet nuget list source
```

次のコマンドはリポジトリルートから実行します。ソース指定の上書きは不要です。

```bash
cd samples/05-dotnet-cache-refresh/Tests
dotnet restore
dotnet build --no-restore
dotnet test --no-build --no-restore
```

実行（`Program.cs`）には実ストアが必要です。**初期読み取り → 標準入力で Enter 待ち →
`cache.RefreshAsync` → 再読み取りして再表示**、という流れです。出力するのは
`TryRefreshAsync` が返す bool ではなく、読み直した実際の設定値です。手順は次節にあります。

## Azure での実行(RBAC とストアのレイアウト)

このサンプルはサンプル01と同じキー（`_shared/*` と `{tenantId}/*`）を読むので、
`script/` で作ったサンプル01のストアをそのまま使えます。アプリが必要とするロールは
**App Configuration Data Reader** だけです。sentinel key の投入と値の変更には、操作する人に
一時的な **Data Owner** を付けます。コマンドはリポジトリルートで実行します。

```bash
RUN01=$(script/bootstrap --sample 01 --azure --sku developer)   # 既存の 01 の run があればその ID を使う
STORE01=$(python3 -c 'import json,sys; print(json.load(open(f".runs/{sys.argv[1]}/outputs.json"))["sharedStoreName"]["value"])' "$RUN01")
SCOPE01=$(az appconfig show -n "$STORE01" --query id -o tsv)
ME=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "App Configuration Data Owner" --scope "$SCOPE01" -o none

# sentinel key を作る（反映まで最大約15分。403 なら待って再実行）
az appconfig kv set -n "$STORE01" --auth-mode login --yes --key "tenant-a/Sentinel" --value "1"
az appconfig kv set -n "$STORE01" --auth-mode login --yes --key "tenant-b/Sentinel" --value "1"
```

別のターミナルで起動します。`[tenant-a] initial LogLevel=Warning` と
`[tenant-b] initial LogLevel=Debug` を表示して、Enter 待ちになります。

```bash
export APPCONFIG_ENDPOINT="https://<STORE01 の値>.azconfig.io"
cd samples/05-dotnet-cache-refresh
dotnet run
```

Enter を押す前に、tenant-a は値と sentinel の両方を、tenant-b は値だけを変えます。

```bash
az appconfig kv set -n "$STORE01" --auth-mode login --yes --key "tenant-a/LogLevel" --value "Error"
az appconfig kv set -n "$STORE01" --auth-mode login --yes --key "tenant-a/Sentinel" --value "2"
az appconfig kv set -n "$STORE01" --auth-mode login --yes --key "tenant-b/LogLevel" --value "Error"
```

30秒以上待ってから Enter を押すと、次のように表示されます。

```text
[tenant-a] refresh check succeeded: True
[tenant-a] refreshed LogLevel=Error
[tenant-b] refresh check succeeded: True
[tenant-b] refreshed LogLevel=Debug
```

tenant-b は、ストアの値が `Error` になっていても sentinel を変えていないため、古い値のままです。
refresh の呼び出しは両方とも `True` なので、値が変わったかは読み直した値で判断します。

終わったら値を戻し、Data Owner を外します。

```bash
az appconfig kv set -n "$STORE01" --auth-mode login --yes --key "tenant-a/LogLevel" --value "Warning"
az appconfig kv set -n "$STORE01" --auth-mode login --yes --key "tenant-b/LogLevel" --value "Debug"
az role assignment delete --assignee "$ME" --role "App Configuration Data Owner" --scope "$SCOPE01"
```

## 既知の制約

- **フェイクストアがありません。** Python サンプル(01〜04)は Azure サブスクリプション
  なしでも `uv run pytest` や `flask run` が動きますが、このサンプルは実 Azure SDK
  (`Microsoft.Extensions.Configuration.AzureAppConfiguration`)を直接使うため、
  正しい入力で `AzureConfigurationRefresher.Load` を呼ぶと実ストアへの接続を試みます。
  単体テストはフェイクの refresher と入力ガードを対象とし、実ストアへ接続しません。
  通信・RBAC・実ストアでの refresh を確認するには、上の Azure 実行手順を使ってください。
- NuGet の復元成功は、実 App Configuration ストアへの接続や RBAC の検証を意味しません
  （[ルート README の既知の制約](../../README.md#既知の制約)参照）。復元後の単体テストは
  Azure 接続なしで実行できます。
