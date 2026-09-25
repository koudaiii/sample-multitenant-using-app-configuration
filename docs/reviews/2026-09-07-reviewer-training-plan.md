# 記事の主張を実Azureで確かめるレビュワー育成プラン

作成日: 2026-09-07。これはテストの適合性レビューと、今後実行する研修の計画です。この作業ではAzureリソースを作成せず、liveテストも実行していません。

**結論: 現在のテストは、記事の一部を裏付ける構成になっています。ただし、記事差分全体を正しいと判定する十分な証拠にはなっていません。** フェイクによる分離・refresh・snapshotの正例と負例に加え、実値を読むliveテストが追加された点は前進です。残る課題は、live手順の権限・接続先の整合、.NET実SDKの更新観測、記事の各主張に対する証拠の対応です。

## 対応状況（2026-09-10 追記）

本計画作成後の作業ツリー（`sample` 側 HEAD `57ac535`）での進捗です。計画本体は今後の研修設計として
そのまま残し、状態のみここに追記します。

- **テスト収集数**: 本計画時 161 collected → **301 collected**。
- **T3（05 の `Program.cs` がロード直後に refresh して終了する）→ 対応済み**。現行 `Program.cs` は
  「初期読み取り → 標準入力で Enter 待ち → `cache.RefreshAsync(tenantId)` → 再読み取りして再表示」
  の継続観測型に変更済み。`Tests/ProgramTests.cs` を追加。`TryRefreshAsync` の bool だけでなく
  読み直した値を出力する。
- **T1 / T2 / T4 / T5 / T6 → 未着手**。`script/bootstrap`・`script/server`・`script/cleanup` と
  `tests/test_scripts.py` は存在するが、操作用 writer と観測用 reader の分離、RUN_ID からの
  subscription/endpoint/RG 突合、live 復旧の try/finally 化、明示 endpoint 時のみの実行ゲート、
  RBAC 反映待ちの設定化は未実装。
- **L0〜L8 のラボ実行 → 未実施**。基礎・拡張とも研修は未開催で、実機ログ・ワークシート記録は無い。
- **C01〜C13 → 実機エビデンス未記録**。geo-replication の別クォータ、Developer の SLA、CMK の
  SKU 条件、Front Door の公開性・refresh 制約、.NET 実 SDK での値変更、認可の負例は
  「文書のみ」または「フェイク」のまま。
- **記事本文の .NET キャッシュ誤説明（memory pressure で unused instances を削除できる）→ 未修正**。
  サンプル側（`src/mtappconfig/cache.py` docstring、ルート README）は訂正済みだが記事は据え置き。

## 1. 対象と到達目標

記事は [koudaiii/architecture-center-pr](https://github.com/koudaiii/architecture-center-pr) の `docs/guide/multitenant/service/app-configuration.md`、base `713d21e075b06e1ca44bfbcbd3f9fee077e46656` → head `5200d6e1c940c30dfff39cea43873e8db76681e6` を対象とします。[固定した差分](architecture-app-configuration-713d21e-5200d6e.patch)を同梱し、隣接checkoutがなくても変更内容を読めるようにしました。

サンプルの確認時点のHEADは `adf80f13e62aefef4e6e1f273162096393b45928`。未コミットのBicep・SDK条件と、未追跡の `script/`・`tests/test_scripts.py` も読んでいます。[入力ファイルのSHA-256一覧](2026-09-07-training-inputs.json)は確認時点の識別用で、実行結果ではありません。研修開始前に必要ファイルをすべて共有可能なコミットへまとめ、そのSHAを別途固定します。並行セッションで変更されたファイルは次のレビュー単位に回します。

`azure-app-configuration` スキルの Configuration / Integrations / Security / Limits の索引を使い、Microsoft Learn MCPから一次資料を取得して照合しました。スキルは資料への入口であり、実装が正しいことの証拠そのものではありません。

受講者の到達目標は、次の5点を自分で説明し、証拠付きのレビューコメントを書けることです。

1. 記事の一文を、観測できる主張に分解する。
2. 何を変え、何を一定に保ち、どの結果なら反証になるかを先に決める。
3. フェイク、実SDK、実サービス、文書照合の証拠を区別する。
4. 成功だけでなく、期待した拒否・更新されないケース・未検証も正しく記録する。
5. 別のレビュワーが同じコミットと独立した環境で再現できるようにする。

## 2. 現在のテストは何を証明するか

今回はassertionと実行経路を静的に確認し、Pythonの `--collect-only` で161件の収集を確認しました。161件成功という意味ではありません。以前の148件成功という結果も、今回追加されたテストの実行結果には流用しません。.NETは6件のFactの内容を確認しましたが、この作業では実行していません。

| 検証対象 | 現在ある証拠 | 判定できること | 判定できないこと |
| --- | --- | --- | --- |
| 01〜03の設定解決 | `tests/test_pattern_contract.py` | 同じ入力データを3配置で読むと同じ期待値になる。共有値の上書き、未登録ID拒否 | 実SDKの選択・trim後の動作、ユーザーのテナント認可 |
| cacheの寿命 | `tests/test_cache.py` と `tests/test_azure_source.py` のcloseテスト | eviction/expiry時のclose呼び出し、指定queryのprovider破棄、共有provider維持 | 実接続の解放、実障害中のHTTP応答、共有providerを含む最大staleness |
| snapshotの正例・負例 | `samples/04-snapshot-references/tests/test_snapshot_references.py` | フェイクでの参照切替、競合順序、混在snapshotの外国テナントキーがマージされること | 実サービス・SDKとの一致、Azureのアクセス拒否 |
| 実ストアのsnapshot | `samples/04-snapshot-references/tests/test_live_snapshot_references.py` | 実行できれば実値の初期解決、tenant-bの初期値、新→旧のrefresh検出 | 後述の前提不足あり。旧→新を同じconfigで再観測、切替中のtenant-b不変、refresh無効の対照、混在snapshotの実機負例 |
| 権限なしの読み取り | liveファイル末尾のskip | 未検証であることを明示 | `--run-live`を付けても常時skipなので、拒否を検証したことにはならない |
| .NET refresh | 05の `TenantConfigurationCacheTests.cs` | 偽refresherへの呼び出し、戻り値の伝播、tenant別オブジェクト保持 | `ConfigureRefresh`の監視登録、SDKが値を変えること、ミドルウェア、tenant sentinelと共有値の更新 |
| bootstrap/cleanup | `tests/test_scripts.py` | 模擬CLIで実行ID分離、接続先生成、初期データ操作、削除対象制限、失敗後のcleanup | AzureがBicep・権限・snapshotを実際に受理すること |
| SKU / geo / CMK / Front Door | READMEと参照資料 | 文書に基づく設計説明 | このリポジトリのコードによる実機証拠はまだない |

### 研修前に直すべき接続上の問題

| ID | 指摘と根拠 | 受け入れ条件 |
| --- | --- | --- |
| T1・P1 | `script/bootstrap:67`以降はData Ownerを一時付与して削除。一方liveテストの `_set_rollout_snapshot_reference()` は `az appconfig kv set --auth-mode login` で書く。記載された手順だけでは、他にデータ書き込み権限を持たないユーザーのrollback操作は許可されない | 操作用writerと観測用readerを分ける。簡易実験で同一IDに一時Ownerを戻す場合は「Data Readerだけで動作確認済み」と判定しない。別途reader-only検証を行う |
| T2・P1 | liveテストの `_subscription()` は環境変数か現在のCLI設定を使用。bootstrapの `--subscription` はその設定を変更しない。READMEはendpointだけをexportする | RUN_IDからsubscription・endpoint・RGを読み、所有タグと対応を照合する。別subscriptionをCLI既定にしても同じrunだけを対象にする |
| T3・P1 | 05の `Program.cs:17`→`:25` はロード直後にrefreshし終了する。30秒経過も外部更新も待たず、refresh後の値も再表示しない | 同じconfig/refresherを保持し、ストア変更前後で値を再読する継続観測ハーネスを用意する。`TryRefreshAsync`のboolだけを成功判定にしない |
| T4・P2 | liveの復旧はfinallyで参照キーを書き戻すだけ。最初の書き込みはtryの外、60秒のループも各SDK/CLI呼び出しの所要時間までは制限しない | 変更前値・content-typeを保存し、書き込みを含むtry/finallyで復元して読み戻す。SDK/CLI単体と全体のtimeoutを設定し、失敗時も元の失敗と復旧結果を別々に残す |
| T5・P2 | liveの `_endpoint()` は指定がなければskip。書き込み先は任意の環境変数で指定可能 | 明示的な実機演習でrun指定・前提が欠けたらBLOCKED/非0終了。一般のunit実行ではskipのままでよい。書き込みは研修用runに限定する |
| T6・P2 | bootstrapのRBAC再試行は最大10分。一方公式はロール反映に最大15分を見込むよう案内する | readiness観測の待ち時間を設定可能にし、反映待ち失敗を記事の反証と取り違えない。[RBACの反映待ち](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-enable-rbac?from=learn-agent-skill) |

記事の.NET cache説明に残る「memory pressureで不要インスタンスを削除できる」という記述は別に修正が必要です。リンク先の `IMemoryCache` は自動でサイズを制限しません。これは差分で新しく導入された誤りではありませんが、今回編集した段落の妥当性を判断するうえで残存指摘です。サンプル側のコメント修正だけでは記事側は直りません。[公式のサイズ管理仕様](https://learn.microsoft.com/en-us/aspnet/core/performance/caching/memory?view=aspnetcore-10.0&from=learn-agent-skill#use-setsize-size-and-sizelimit-to-limit-cache-size)

## 3. 記事の主張と必要な証拠

主張ごとに、`文書照合済み / フェイク確認済み / 実機確認済み / 未検証 / 要修正` を別々に記録します。コードの変更が済んだ状態は、実機確認済みとは扱いません。

| 主張ID | 記事差分から取り出した主張 | 現状 | 演習・判定方法 |
| --- | --- | --- | --- |
| C01 | Standardではレプリカごとの別クォータとproviderの負荷分散を利用できる | 文書のみ | L6で実レプリカと接続先分散を観測。クォータの制度は公式資料で確認 |
| C02 | geo-replicationだけではnoisy neighborを防げず、テナント別制限・監視が必要 | 文書のみ、アプリ制限未実装 | L6でtenant別計測と制限有無を比較。単なるHTTP件数とApp Configurationへの要求件数を区別 |
| C03 | DeveloperはSLAなし・低負荷の非本番向け。Premiumはrequest quotaなし | 文書で確認可能 | FAQを版・確認日付きで引用。短時間の稼働や少数要求からSLA・無制限を証明しようとしない |
| C04 | CMKはStandard/Premium、異なるCMKには別ストア | 文書のみ、暗号化設定未実装 | L7で2ストアと2キーの設定を確認。暗号文の内部実装まで観測したとは主張しない |
| C05 | providerはcacheする。.NETでは登録と明示的トリガーが必要 | Pythonはフェイク、.NETは委譲テスト | L4で登録/トリガーを有効・無効にした対照実験 |
| C06 | tenantごとに別のconfigurationを遅延ロード・cacheできる | フェイクで確認 | L1/L4で同じプロセスのtenant-a/bとロード回数を観測。サイズ制御は別の実装条件 |
| C07 | snapshot参照の変更で再デプロイせずroll forward/backできる | フェイクと未実行live | L2で新→旧→新を同じプロセスで観測、tenant-bを全段階で比較 |
| C08 | 参照キーのprefix/labelはsnapshotの内容をfilterしない | フェイク負例あり | L3でprefixとlabelの実機負例を独立して確認 |
| C09 | snapshotのサイズ上限を考慮する | 文書のみ | 公式上限と実snapshotのsizeを突き合わせる。上限超過試験はL3拡張 |
| C10 | snapshot参照はストアの認可境界を変えず、snapshot読み取り権限が必要 | live拒否は常時skip | L5でreader-onlyと権限なしのIDを区別し、fresh readで判定 |
| C11 | Front Doorはpreview・public cloud向けで、匿名公開する専用ストアを使う | 文書のみ | L8で匿名アクセスを確認。秘密の代わりに無害な識別文字列を使う |
| C12 | Front Doorはsentinel refresh非対応、全選択キーを監視。edge cacheとclient refreshのため結果整合 | 文書のみ | L8で同じURLのcache/refresh時系列を記録。即時反映や厳密な最大伝播時間を保証しない |
| C13 | `ms.date`、AI利用メタデータ、表の書式 | 記事編集事項 | 出版手順・lint・リンク確認で判定。実機試験の対象外 |

tenant-aだけの確認で、cohort・stampの運用すべてを検証済みにはしません。コホート実験が必要なら、同一の非機密configurationを共有する2つの利用者と、対象外の1利用者を用意し、同じ参照先変更が対象の2者だけに反映される別ケースを追加します。

## 4. 研修の進め方

講師は環境と記録方法を準備し、受講者が操作前に予想を書きます。1人目が操作、2人目が証拠から判定し、次の演習で役割を交換します。講師は期待した出力を誘導せず、「どのassertionがどの主張を裏付けるか」を確認します。

基礎回はL0〜L5を半日〜1日、拡張回はL6〜L8を別日に行う想定です。これは研修時間の目安で、デプロイ・RBAC・CDNの待ち時間は別です。SKUや料金は開催時の見積りを記録し、作成するRG・ストア・レプリカ数を先に決めます。

L0〜L5の完了でsnapshotとcacheの中核は判断可能になります。L6〜L8を省略する場合、対応する記事部分は「一次資料に基づくレビュー、実機未検証」として残します。スコープ外の全Azure機能を実装することは研修のゴールではありません。

### L0 — 対象を固定し、証拠を読める環境にする

**講師の準備:** T1〜T6を解消した小さな研修用変更を先に用意します。現在の `script/bootstrap/server/cleanup` は01〜04用で、05・Front Door・CMK・レプリカはまだ準備しません。既存のAKSコンテキスト名は実験先の根拠にせず、App Configuration用のsubscription・region・RUN_IDを明示します。基礎演習にAKSは不要です。

**受講者の操作:** 固定コミットを新しい作業ディレクトリに取得し、次を記録します。

```bash
git rev-parse HEAD
git status --short
uv --version
python3 --version
az version
az bicep version
dotnet --info
uv run pytest --junitxml=unit-results.xml
dotnet test samples/05-dotnet-cache-refresh/Tests/Sample05.Tests.csproj --logger trx
```

このコマンド群は通常テストで、liveを起動しません。NuGet等の復元失敗は環境準備失敗として記録します。別のコミットのビルド成果物を再利用して成功扱いにしません。Azure試験前には `.venv/bin/python -m pip` の存在を仮定せず、`uv pip freeze --python .venv/bin/python` で実SDKの解決版も保存します。

**合格条件:** 受講者が、通常テスト成功とliveのskipを別々に報告し、「この結果だけではC07の実機動作は未確認」と説明できる。

### L1 — 初期データと観測経路を確かめる

次のコマンドは既存スクリプトで利用可能です。まだT1〜T6未修正のliveテスト一式は起動しません。別ターミナルでHTTPを読みます。

```bash
RUN_ID=$(script/bootstrap --azure --sample 04 --subscription "$TRAINING_SUBSCRIPTION" --location "$TRAINING_LOCATION")
script/server --run "$RUN_ID" --port 5104
```

`TRAINING_SUBSCRIPTION` と `TRAINING_LOCATION` は講師が割り当てた値です。`outputs.json` のendpointとPortal/CLIのリソースIDが一致すること、実SDKが入っていること、環境変数で別IDが選ばれていないことを確認します。

| 観測 | 初期の期待値 |
| --- | --- |
| tenant-a | LogLevel=`Debug`、BetaDashboard=`true`、DisplayName=`Tenant A (rollout)`、DatabaseName=`db-tenant-a` |
| tenant-b | LogLevel=`Debug`、BetaDashboard=`true`、DatabaseName=`db-tenant-b`、SupportEmail=`vip@contoso.example` |
| 共通 | App:Version=`1.4.2` |

`/readyz`の200だけではseedやsnapshotが正しい証拠になりません。APIのvaluesとストア側のキー・参照content-type・snapshot内容を照合します。01〜03の比較は、必要に応じて別RUN_IDで作成して `expected_config` 相当の全値を比較します。03の3ストアを使う際は、同じ値が返ることと別ストアへの認可を分けて判定します。

**問い:** 「APIがdictを返した」だけでは、どの誤設定を見逃すか。**期待する回答:** 空ストア、誤ったprefix/label、参照未解決、別endpoint等。

### L2 — 同じプロセスで新→旧→新を観測する

**環境:** L1の専用ストア、操作用writer、読み取り専用reader、継続稼働する同一のsource/config。writerは参照を書き換え、readerは値だけを観測します。独立したCLIプロファイルまたは明示したcredentialを使い、各IDの実効ロールを記録します。現在のbootstrapは同一のローカルユーザーを想定するため、ID分離はT1の準備作業です。

**手順:** 開始時の参照・content-type・tenant-a/b全値・プロセスIDと起動時刻を保存。参照先だけを旧snapshotへ変更し、同じconfigのrefreshを継続。旧値になった後、新snapshotへ戻して同じ観測を繰り返します。各段階でtenant-b全値を読みます。

| 段階 | tenant-a LogLevel | BetaDashboard | DisplayName | tenant-b |
| --- | --- | --- | --- | --- |
| 新 | Debug | true | Tenant A (rollout) | 初期値と完全一致 |
| 旧へ変更後 | Warning | false | Tenant A | 初期値と完全一致 |
| 新へ戻した後 | Debug | true | Tenant A (rollout) | 初期値と完全一致 |

**対照実験:** refreshを呼ばないconfigは参照変更後もロード済みの値を保持する。refresh有効・無効でプロセス再起動やTTL再ロードが混ざらないよう、直接SDKを使う短い観測ケースを分けます。Flask経路ではTTL expiryが起きていないことをdiagnosticsで確認し、provider再構築だけで更新した結果をrefreshの証拠にしません。

**合格条件:** 値が全期待値に一致し、同一のプロセス・configで、再ビルド・再起動・再デプロイをせずに両方向へ変わる。SDKのboolやHTTP 200だけでは合格にしません。ネットワーク呼び出し単体と全体に期限を設定し、期限超過時はエラー・最後の値を残します。

**育成上の焦点:** 起動後の一回読み取りと動的更新を区別できるか。[snapshotのrefresh仕様](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-snapshot-references?from=learn-agent-skill#refresh-behavior)

### L3 — スコープとサイズの反例を確認する

L2の正常snapshotを保持したまま、専用run内に `tenant-a/ReviewValue=A` と `tenant-b/ReviewCanary=PUBLIC-DEMO-B` を含む新しい混在snapshotを作ります。秘密は使いません。tenant-a配下の参照キーをこのsnapshotへ向けます。

**期待値:** tenant-aの選択結果にも `tenant-b/ReviewCanary=PUBLIC-DEMO-B` が現れる。この負例を正しく観測できたことがC08の確認であり、読者のアプリの認可が安全という意味ではありません。参照キーだけをscopeしても内容は絞られない、という警告を証明します。

prefixのケースが終わったら、参照キーをtenantラベルで選択する別ケースを追加し、同じ負例を確認します。選択ロジックが異なるためprefixの結果をそのままlabel検証済みにしません。最後に正常参照へ戻し、他テナントのcanaryが消えるまで同じconfigで確認します。

サイズは公式の **snapshot単体1 MB** と、SKUごとの **snapshot総容量** を区別します。通常はsnapshotのsizeメタデータと公式上限の照合までで十分です。上限超過の拡張試験をする場合は、1件10 KB未満の無害な設定を複数用意し、合計が単体snapshot上限を超えるケースを作り、返された制限理由を記録します。個別key-valueの10 KB制限やストア総容量違反をsnapshot上限の検証と取り違えません。[制限表](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits?from=learn-agent-skill#azure-app-configuration)

**問い:** 参照キーのprefixを狭めれば、混在snapshotの漏出は止まるか。否と答え、実測値から理由を示せること。

### L4 — .NETの「登録」と「トリガー」を分けて確認する

**研修前の小さな追加実装:** 05の実 `AzureConfigurationRefresher.Load()` を呼び、同じ `TenantConfigEntry` を保持し続ける対話式コンソールまたはオプトイン統合テストを用意します。操作後の値・refresh試行・時刻を出力し、明示的なreadとrefreshを別々に呼べるようにします。これは現行Programにまだありません。Webサンプルを大きく作り直す必要はありません。

sample 01で用意した専用ストアにtenant-a/bのSentinelを追加し、次を順に実施します。

| ケース | 操作 | 期待する観測 |
| --- | --- | --- |
| A | cache.Get(a)を2回、Get(b)を1回 | tenantごとのロード1回、a/bのconfigは別。単なる辞書の値ではなく実SDKで読めることも確認 |
| B | aのLogLevelとSentinelを更新。refreshを呼ばず間隔以上待ち、configを読む | 旧値のまま。待つだけで変わるという説明を反証 |
| C | Bの同じconfigでrefreshを呼ぶ | aの値が新値へ変わる。bは不変。返るboolがtrueでも変更検出とは限らないので値をassert |
| D | ConfigureRefreshを登録しないproviderで同じ操作 | 呼び出しだけでは動的更新にならないことを確認。SDK版の実際の戻り値・診断も記録 |
| E | aの通常キーだけを変更しSentinelは変えない。間隔後にrefresh | 現行05のsentinel監視では旧値。Sentinelを変更後は新値 |
| F | `_shared/App:Version` を変更、aのSentinelだけを更新 | aは新しい共有値、bは旧共有値。bのSentinelも更新するとbも変わる |

`refreshAll:true` はそのproviderが選択した共有キーも更新対象に含みます。全テナントの別providerを一括更新する意味ではありません。tenant-bのSupportEmail上書きも実測し、trim後の競合解決を未検証のまま「01と同一」としません。[.NET providerの登録・sentinel仕様](https://learn.microsoft.com/en-us/azure/azure-app-configuration/reference-dotnet-provider?from=learn-agent-skill#configuration-refresh)

**ミドルウェア経路を実機確認済みと言う場合:** 別の最小ASP.NET Core fixtureを追加し、DIの `AddAzureAppConfiguration()` と `UseAzureAppConfiguration()` を配置。idle中は更新されず、要求が来るとチェックされることを観測します。最初の要求が古い値を返しても直ちに失敗にしません。refreshは要求処理と非同期で、後続要求で反映されるためです。動的に作ったtenant別providerが自動でDIのrefresh対象になるとは仮定せず、どのrefresherが登録されるかも確認します。未実装なら「資料照合のみ」のままです。[ミドルウェアの動作](https://learn.microsoft.com/en-us/azure/azure-app-configuration/enable-dynamic-configuration-aspnet-core?from=learn-agent-skill)

### L5 — 認可と障害時のcacheを取り違えない

講師はoperator、reader、no-accessの3つのIDを準備し、どのcredentialが実際に使われたかを識別可能にします。readerにData Readerのみ、no-accessに対象ストアのdata actionを付与しない構成にします。グループ所属・親scopeからの継承権限も確認します。

| 試験 | 判定 |
| --- | --- |
| readerによるfreshな通常設定とsnapshotの読み取り | 成功し、期待する値が返る |
| readerによる設定書き込み | データ操作として拒否される |
| no-accessによるfreshな読み取り | 認証はできるがデータ認可で拒否される。期限切れcredentialやDNS障害を同じ拒否として採点しない |
| 同じ共有ストアのData Readerでtenant-bのキーを直接選択 | 読める。prefix/labelはAzure RBACのテナント境界ではない |
| 03のtenant-aストアだけにData Readerを付けたIDでtenant-bストアを読む | 拒否される。ストア分離が許す権限分離を確認 |

権限なしのテストはfreshなproviderで行います。ロード済み値がcacheから返ることを「RBACが無効」と誤判定しません。書き込み用CLIと読み取り用SDKで同じIDが使われたと決めつけず、明示したcredentialを使用します。アクセストークン自体は証拠に保存しません。

**障害試験は別ケース:** 演習プロセスだけの通信を制御できるproxy等でstoreへの通信を遮断し、warm refresh、cold load、TTL超過を比較します。RBAC変更の反映待ちを即時障害注入の代用にしません。03ではtenant-aだけを遮断し、tenant-bの応答と共有ストアのreadyzを同時観測します。現在のcacheは全体lockを持つため、tenant-aの遅いloadでbも待つ可能性を測定します。shared providerはtenant evictionで破棄しないので、共有設定までstalenessが制限されると判定しません。これらは記事固有の追加主張ではなく、このサンプルの信頼性説明を裏付ける回帰試験です。

### L6 — geo-replicationとnoisy neighborを観測する（拡張）

現行Bicepにレプリカはありません。講師が別の小さなfixtureでStandard＋1レプリカを作り、更新値が両方で読めるまで待ちます。実SDKの負荷分散を明示的に有効にした場合と無効の場合で、送信先endpointの診断を比較します。自動レプリカ検出だけでは、通常時に負荷が分散するとは限りません。[geo-replicationと負荷分散](https://learn.microsoft.com/en-us/azure/azure-app-configuration/howto-geo-replication?from=learn-agent-skill#load-balance-with-replicas)

アプリへのHTTP負荷はcache hitになり得るので、providerからストアへ実際に出た要求をtenant別に計測します。2テナントの負荷を一定期間変え、アプリ側制限なし/ありでbの待ち時間・エラー・ストア要求を比較します。現行アプリにtenant別rate limitやメトリクスはないため、準備段階でfixtureに追加する必要があります。実験の上限要求数と実行時間を決め、結果が出なければ無制限に負荷を増やしません。

レプリカ別クォータやPremiumのquota制度は公式資料で判定します。数分間429が出ないことは、無制限の証明ではありません。Premiumにもthroughput allowanceがあり、request quotaなしを「429が絶対出ない」に言い換えません。[FAQ](https://learn.microsoft.com/en-us/azure/azure-app-configuration/faq?from=learn-agent-skill#which-app-configuration-tier-should-i-use)

### L7 — CMKとSKUを確認する（拡張）

現行BicepはCMK・Key Vault・store identityを構成しません。専用の小さなfixtureでStandard/Premiumの2ストア、2つのKey Vault key、store identityとwrap/unwrapの権限を準備します。Azureが設定を受理し、各storeの暗号化プロパティが意図した別key URIを参照すること、両ストアの設定を読めることを証拠にします。

DeveloperのSLAがないことやCMK対応SKUは公式資料との照合で確定します。講習時間内に障害が起きなかったことからSLAを判断しません。key失効の影響まで確認する追加ケースは、鍵のキャッシュを考慮した別の長時間試験とし、「権限を外して即座に読めなくなる」と仮定しません。[CMKの要件・動作](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-customer-managed-keys?from=learn-agent-skill)

### L8 — Front Doorの匿名性と更新遅延を確認する（拡張）

01〜04のtenant用ストアを公開に流用せず、無害な `Public:ReviewVersion=v1` だけを置く専用storeを使います。実験時点で対応する.NET/JavaScript provider版を固定し、Front Door用の設定、read-only managed identity、cache、originを別fixtureで準備します。現行Pythonアプリとbootstrapだけでは実行できません。

認証情報のないクライアントから公開設定が読めることを確認し、別のroute/prefixを指定できることがユーザー認可ではないと説明します。warmな同じURLに対しv1→v2を変更し、originの変更時刻・edgeの応答・client refresh時刻・観測値を記録します。cache-busting queryや手動purgeを途中に入れません。全選択キー監視で収束することを観測し、sentinel監視は非対応として公式資料と照合します。[クライアントproviderの制約](https://learn.microsoft.com/en-us/azure/azure-app-configuration/how-to-load-azure-front-door-configuration-provider?from=learn-agent-skill)

**時間の採点:** 設定TTL＋refresh間隔をサービスの厳密なSLAにしません。早期cache evictionやorigin障害時のstale応答があり得るため、想定より早い更新・遅い更新はcache headersとログから説明します。記事の「cache expires後」という説明を具体的な最小/最大秒数の保証に拡大しないことが重要です。[Front Doorのcacheと例外](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-hyperscale-client-configuration?from=learn-agent-skill#caching)

## 5. 証拠の形式と合格条件

[受講者用の記録テンプレート](reviewer-lab-worksheet.md)を主張ごとにコピーします。証拠にはcommit、RUN_ID、SDK/CLI版、実験前提、操作、期待値、実測値、時刻、判定を含めます。ロールのscope/IDは制限した記録に保存し、公開版ではcredentialと識別情報を適切に除きます。テスト結果のJSON/XML、標準出力、SDK診断の該当箇所を根拠にし、スクリーンショットだけに依存しません。

判定語は **PASS / FAIL / BLOCKED / NOT RUN / DOCUMENTED ONLY** に統一します。認可の負例で期待通り拒否された場合はPASS。前提不足でskipしたケースはNOT RUNまたはBLOCKEDです。失敗をskipへ変えて合格率を上げないことを明示します。

最後に元の参照・一時権限を復元し、serverを停止して各RUN_IDで `script/cleanup --run "$RUN_ID"` を実行します。スクリプトは対象RGの削除完了を待ちます。別runが残っていること、同じcleanupを繰り返しても問題ないことも確認します。L5〜L8のfixtureがRG外に作ったID・role assignment等は別の一覧で片付けます。soft delete対象のKey Vault/App Configurationを完全purgeしたと報告しません。削除結果と残存物一覧も証拠の一部です。

| 育成の採点軸 | 合格する説明 |
| --- | --- |
| 主張の切り分け | 対象の一文とassertionが対応し、記事にない要件を勝手に足さない |
| 対照と反証 | 何を無効にすると更新されなくなるか、どの負例なら記事を疑うかを先に説明できる |
| 境界の理解 | cache hitとservice read、認証と認可、selectorとRBAC、boolと値変更を区別できる |
| 再現性 | 別レビュワーが別RUN_IDで同じ手順・期待値を再現できる |
| 判断の節度 | フェイク・コンパイル・文書照合を実機検証と呼ばず、未検証をそのまま残す |

卒業課題は、講師が研修用コピーで1つだけ条件を変えたケース（refresh無効、誤ったselector、別subscription、混在snapshot等）を渡し、受講者が原因・記事への影響・最小の修正提案を報告することです。正解コードを暗記するのでなく、証拠から「どこまで承認できるか」を判断できれば合格です。

## 6. 巨大なブランチをこれ以上広げない進め方

1. **証拠と実行前提の変更:** T1/T2/T4/T5/T6をlive fixtureに集約し、RUN_ID・identity・復旧・結果出力を整える。記事本文や通常アプリの設計は同時に変更しない。
2. **snapshotの検証:** 既存liveを拡張してL2/L3/L5を対応付け、個別の結果ファイルでレビューする。
3. **.NETの検証:** 継続観測ハーネスと登録/トリガー対照を独立させる。ミドルウェアは実装する場合だけ別ケースにする。
4. **geo / CMK / Front Door:** 各fixtureを独立した追加変更として扱い、未実施でも文書照合という判定を保持する。
5. **記事の最終修正:** C01〜C13の判定を見て、誤記を直すか、説明を条件付きにするか、そのまま承認するかを一文ごとに決める。

各変更のレビュー資料は「主張ID、操作、期待値、実測値、判定、制約」を1ページに収めます。全履歴を読ませず、固定差分と必要な証拠へ案内することが、レビュワーを育てながら大きな変更を扱うための基本方針です。
