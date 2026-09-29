# 第三者による検証可能性のレビュー — 2026-09-06

現状は、分離モデルの比較と snapshot rollout の概念を理解する教材として有用です。ただし、記事差分全体の妥当性を第三者が再現可能な形で確認する証拠としては、以下の不足があります。テスト成功を、実 Azure の動作や認可の検証成功と解釈できない点が重要です。

レビュー対象は sample repository の `e864600768caa1fa89c6b03ac40d2c48cf42a4b9` と確認時点の未コミット README、architecture-center-pr の `713d21e075b06e1ca44bfbcbd3f9fee077e46656..5200d6e1c940c30dfff39cea43873e8db76681e6` です。別セッションが作業中のため、その後の変更は含みません。未追跡の `AppConfiguration/` は配布されるサンプルの一部と扱っていません。05 は確認開始時点では設計段階で、レビュー中に未追跡の実装ディレクトリが追加されました。進行中の実装は今回の検証対象に含めていません。

`uv run --offline pytest` は **138 passed, 1 skipped**。さらに `git archive HEAD` で追跡ファイルだけを一時ディレクトリに取り出し、既存の Python 3.14.3 / pytest 9.1.1 環境から実行して、同じ結果を確認しました。これは未追跡ファイルに依存しないことの確認であり、空の依存キャッシュからのインストール検証ではありません。Azure リソースの作成、実 SDK での通信、他セッションへのメッセージ送信は行っていません。

01〜04の `main.bicep` はインストール済みの Bicep CLI でコンパイル成功し、生成した4ファイルが ARM JSON として読み取れることも確認しました。これはデプロイ成功、サービス側の診断設定の受理、RBACの実効性を証明するものではありません。

優先度は P1＝検証済みとして共有する前に対応、P2＝再現性・説明の正確性のために対応、とします。既存 README の ToDo にあるものも独立して確認しました。

## 対応状況（2026-09-29 更新）

2026-09-11 以降に状態が変わった指摘だけを記録します。変わっていない指摘は下の表のままです。

| 指摘 | 状態 | 変化 |
| --- | --- | --- |
| 5. live テストが記事の中心主張を検証できない | 一部対応（前進） | `script/bootstrap --azure` で 01〜04 を実ストアに作り、実 SDK で tenant-a/b の値を読めることを手動で観測した。sample 04 は稼働中のサーバーのまま参照キーを旧スナップショットへ書き換え、tenant-a だけが旧値に戻り tenant-b は変わらないこと、ロールフォワードで戻ることを観測した。bootstrap は投入後に Data Owner を外すため、サーバーは Data Reader の割り当てだけで読んだ。ただし継承を含む実効ロールは検査していないので、reader-only の証明ではない。.NET（sample 05）は sample 01 のストアで、値と sentinel を更新した tenant-a だけが `RefreshAsync` 後に新しい値になり、値だけを変えた tenant-b は古い値のままであることを観測した。**未実施**：権限なしの拒否 |
| 6. 検証対象を固定できず、チェックリスト対応が不正確 | 一部対応（前進） | ルート `README.md` の冒頭に、コミット `bef1a19` の変更点ごとの「確かめる場所・確かめ方」表を置いた。sample 04 は提案時の checklist の代わりに、記事の各文とテストの対応表にした。**未了**：主張 ID との対応、固定 diff の復元、入力マニフェストの更新 |
| 7. .NET の既存誤説明（IMemoryCache） | 判断を変更 | 記事の文は "the cache **can** remove unused instances" で、アプリが `Compact` / `Remove` などで削除を用意すれば成り立つ。誤りではなく「削除の主体がアプリだと読み取れない」記述として、ルート `README.md`「記事の補足: メモリ圧迫時の削除はアプリケーションが行う」で扱う。記事側は、主体を明記する編集を提案するかを改めて判断する |

下の表が参照する README の節は移動しています。信頼性・セキュリティ・タイムアウトの説明は
[`docs/implementation-notes.md`](../implementation-notes.md)、ルート README の「.NET」節は
「記事の補足: メモリ圧迫時の削除はアプリケーションが行う」です。

## 対応状況（2026-09-10 追記、2026-09-11 再確認）

以下は本レビュー後の作業ツリー（`sample` 側 HEAD `57ac535`、記事側ブランチ
`koudaiii/aac-pipe-fresh-multi-tenant-app-configuration` = `59eea712f3`）に照らした状態です。
本レビューが対象にしたサンプルコミット（`e864600` / 未追跡実装）は rebase により現在の履歴には
残っていません。テスト収集数はレビュー時 138 passed から **301 collected** に増えています。
原文の指摘はそのまま記録として残し、状態のみここに追記します。

2026-09-11 に 6 つの README を再確認しました。
[README 簡素化タスク](2026-09-10-readme-simplification-tasks.md)は全件未完了で、ルート README の
`## 検証状況` も未追加です。そのため、指摘 6 の「一部対応」は変更しません。

| 指摘 | 優先度 | 状態 | 対応内容・根拠 |
| --- | --- | --- | --- |
| 1. LRU 上限が SDK provider に効かない | P1 | 対応済み | `azure_source.py` を query 単位の `_ProviderResources` に再設計。`close(key_filter,label,trim)` が当該 provider を破棄し、`cache.py` の TTL 失効／LRU eviction が `entry.config.close()` 経由で呼ぶ。borrower lease で使用中の provider は閉じない。共有 provider がテナント TTL の対象外である点は README 信頼性節で明示 |
| 2. TTL・障害時保証がフェイクと実接続で乖離 | P1 | 対応済み | `select()` は初回ロード失敗を `ConfigStoreUnavailableError` に包んで raise。refresh 失敗は `on_refresh_error` / `refresh_errors` で通知し `cache.py` が記録。README 信頼性節に「古さの上限は独立タイマーではなく close/recreate が与える」「`max_staleness_seconds` は提供しない」「`refresh()` の返却だけを通信成功の証拠にしない」を明記 |
| 3. mixed-snapshot の反例が検証表に無い | P1 | 対応済み | `samples/04-snapshot-references/README.md` に「重要な注意: 参照キーのスコープはスナップショットの中身をフィルタしない」節。負例テスト `test_fake.py::test_a_snapshot_containing_a_foreign_key_merges_it_in_unfiltered` と `test_snapshot_references.py::test_a_snapshot_containing_another_tenants_keys_leaks_them_unfiltered` |
| 4. SDK 最低版が sample 04 要件未満 | P2 | 対応済み | `requirements-azure.txt` を `azure-appconfiguration-provider>=2.5.0` に（レビュー指摘の 2.4.0 以上）。理由コメントを併記 |
| 5. live テストが記事の中心主張を検証できない | P1 | 一部対応 | `tests/test_live_snapshot_references.py`（`@pytest.mark.live`、既定 skip）を追加し、参照解決・別テナント不変・`az appconfig kv set` による実書換後の refresh 検出・ロールバック・ロールアウト復帰を検証。投入データ／コマンド／期待値／timeout／検証版／reader-only 実行手順を README 化。**未実施**：読み取り権限なしの拒否、reader-only の実効権限確認、.NET 実 SDK での値更新観測（研修計画 L4/L5 側） |
| 6. 検証対象を固定できず、チェックリスト対応が不正確 | P2 | 一部対応 | sample 04 README はチェックリスト 8 項目を正確に引用し、6 項目を対象外と明示、2 項目を対応表で裏付け。入力ハッシュと主張表 C01–C13 は本ディレクトリに用意。**未了**：固定 diff `architecture-app-configuration-713d21e-5200d6e.patch` の復元、ルート `README.md` への「repo URL / base-head SHA / 主張 ID / 検証種別」単一対応表、`docs/reviews/` 自体のコミット、入力マニフェストの現 HEAD への更新 |
| 7. .NET の既存誤説明（IMemoryCache／背景 refresh） | P2 | 一部対応 | サンプル側は訂正済み（`src/mtappconfig/cache.py` docstring とルート `README.md`「.NET」節が「メモリ圧迫で自動削除されない」「背景だけでは refresh されない」を明記）。**未修正**：記事本文 `app-configuration.md`（`59eea712f3` 約 98 行目）に "the cache can remove unused instances if your application is under memory pressure" が残存 |
| 8. HTTP テナント認可の省略が利用者に不明示 | P2 | 対応済み | ルート `README.md` セキュリティ節に「レジストリ照合は入力検証であり**認可ではない**」「本番は認証済みコンテキストからテナントを導出し認可せよ」。`webapp.py` `_resolve` docstring も同旨 |
| 9. README の順次実行導線が途切れる | P2 | 対応済み | sample 04 README は全コードブロックに作業ディレクトリ（リポジトリルート）と明示 `PYTHONPATH=src:samples/04-snapshot-references` を記載。ロールアウトのスニペットは「自己完結・起動中 Flask に影響しない・前後の値を表示」と明記 |
| 追加: フェイク seed と Azure CLI 手順のデータ不統一 | – | 対応済み | sample 04 README §「ストアの中身」で snapshot 内容・直接キーの旧ベースライン・tenant-b の missing reference をフェイク／CLI で一致させ、`test_fake_seed_matches_the_documented_live_seed_baseline_and_snapshots` が突き合わせ |

残課題は #5・#6・#7（記事側）と、下記研修計画の live/実 SDK エビデンスです。
このうち #6・#9 と各 README の冗長さは、ファイル単位で並行実行できるタスクに分解して
[2026-09-10 README 簡素化タスク](2026-09-10-readme-simplification-tasks.md)にまとめています。

---

1. **[P1] LRU の上限が SDK provider に適用されない。**

   対象: `src/mtappconfig/azure_source.py:64`, `:101`、`src/mtappconfig/cache.py:151`、README のパフォーマンス説明。

   外側のエントリーを削除しても `_providers` は query ごとの provider を保持します。既存テストの `_FakeSdk` をアダプターに注入し、`max_entries=1` で3テナントをロードすると、外側は1件、provider は共有用を含め4個、`close()` 済みは0個でした。固定の2テナントでは目立ちませんが、テナント数を増やすと「LRUでメモリを有界に保つ」という設計上の保証が成立しません。

   完了条件: provider の所有者と破棄タイミングを決め、共有 provider を壊さずに eviction と連動させる。外側の件数だけでなく、残存 provider 数と解放を検証する。

2. **[P1] TTL と障害時の保証がフェイクと実接続で異なる。**

   対象: `src/mtappconfig/azure_source.py:101`、`src/mtappconfig/cache.py:98`、README の信頼性説明。

   TTL 後の `source.load()` も以前の provider を再使用します。SDK seam で初回ロード後の新規 load をすべて失敗させても、TTL 超過時に新規 load は呼ばれず、古い値が返りました。この再現は実ネットワーク障害そのものを検証したものではありませんが、provider を使い回す経路を証明します。実 Python provider は refresh 失敗時にもキャッシュを使い続けるため、README の「TTL切れ後は503」「TTLがstalenessを制限する」はこのアダプターには保証されません。[Python provider の refresh 仕様](https://learn.microsoft.com/en-us/azure/azure-app-configuration/reference-python-provider#configuration-refresh)

   完了条件: TTL をメモリ管理の期限とするか、許容する古さの上限とするかを明記する。後者なら最終成功時刻を管理し、実接続でも期限超過後の期待 HTTP 応答を検証する。

3. **[P1] snapshot のスコープに関する重要な反例が検証表から欠けている。**

   対象: `samples/04-snapshot-references/README.md:33`、同サンプルの `test_tenant_isolation_is_preserved`、記事の98行目。

   現在のテストは正しいデータ配置で値が混ざらないことを確認しています。参照キーのスコープが snapshot の内容を絞り込むわけではない、という記事の注意は直接検証していません。実際にフェイクで `tenant-a/RolloutSnapshot` を、`tenant-b/DatabaseName` も含む snapshot に向けると、tenant-a の解決結果に `tenant-b/DatabaseName=db-tenant-b` が入ります。これは意図する provider モデルに沿った動作であり、フェイク側で再フィルターして隠すべき問題ではありません。[参照先のキーをマージする仕様](https://learn.microsoft.com/en-us/azure/azure-app-configuration/reference-python-provider#snapshot-reference)

   完了条件: 混在 snapshot の負例、正しくスコープした snapshot の正例、両方をテスト・READMEに対応付ける。「参照キーの選択」「snapshot の内容」「ストアの認可」を区別する。

4. **[P2] SDK の最低バージョンが sample 04 の要件を満たさない。**

   対象: `requirements-azure.txt:6`。

   `azure-appconfiguration-provider>=2.0` は snapshot reference 非対応の版を許可しています。公式の最低版は **2.4.0** です。新規インストールで新しい版が選ばれる場合もありますが、既存環境に旧版が入っていると要件を満たしたまま意図した動作になりません。[対応バージョン](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-snapshot-references#language-availability)

   完了条件: 最低版を合わせ、検証した SDK 版を結果に記録する。依存関係の制約と実行検証済みの版を区別する。

5. **[P1] live テストが成功しても、記事の中心的な主張を検証できない。**

   対象: `tests/test_azure_source.py:160`、sample 04 の Azure 手順。

   唯一の live テストは到達性と戻り値が dict であることだけを確認し、空のストアでも成功します。参照の解決、旧→新→旧の切り替え、refresh の検出、別テナント不変、読み取り権限の有無はいずれも確認しません。SDK seam の `_FakeProvider.refresh()` も回数を増やすだけです。フェイクの期待動作と同じ期待値をテストしているだけでは、実 SDK の互換性の証拠になりません。

   完了条件: オプトインの実 SDK 検証手順を用意し、投入データ、実行コマンド、各段階の期待値、refresh を待つ期限、検証した版を記載する。実行していない項目は「未検証」とする。自動書き込みを必須にする必要はなく、手動操作＋読み取り側の assertion でも第三者は検証可能になる。

6. **[P2] 検証対象を固定できず、チェックリストの対応関係も正確でない。**

   対象: `README.md:51`、`samples/04-snapshot-references/README.md:16`。

   ルートの比較手順は隣接 checkout と可変の `HEAD` を要求し、その取得元・対象 head がありません。第三者がこのサンプルだけ clone しても同じ差分を得られません。また `[x]` は「テスト成功」と「対象外として文書確認」を兼ね、完了数が動作検証の網羅率に見えます。sample 04 が列挙する definition of done は、隣接 `report.md` の実際の4項目と一致しません。例えばクロスリンク追加・表記ゆれ確認は、元の4項目にはありません。RBAC の説明に挙げたフェイクの分離テストも、Azure が権限を拒否することは確認していません。

   完了条件: 対象 repository URL、base/head SHA または固定 diff、主張ID、一次資料、検証種別、テスト・手順、結果を1つの対応表にする。種別は「フェイク実行」「実SDK実行」「IaCコンパイル」「文書照合」「対象外」「未検証」など明確に分ける。チェック項目は元の文書から正確に引用または要約する。

7. **[P2] .NET に関する既存の誤説明が残っている。**

   対象: `../architecture-center-pr/docs/guide/multitenant/service/app-configuration.md:92`、`src/mtappconfig/cache.py:7`。

   記事はリンク先の in-memory cache がメモリ圧迫時に不要なインスタンスを削除するかのように説明していますが、ASP.NET Core の `IMemoryCache` はメモリ圧迫に基づいてサイズを自動制限しません。開発者がサイズや期限を設定する必要があります。これは今回導入された誤りではなく既存記述の残存ですが、全体を検証する際は修正対象です。Python の docstring にある「.NET provider はバックグラウンドで refresh する」という無条件の対比も、今回の記事の明示的トリガーという説明と一致しません。[IMemoryCache のサイズ管理](https://learn.microsoft.com/en-us/aspnet/core/performance/caching/memory?view=aspnetcore-10.0#use-setsize-size-and-sizelimit-to-limit-cache-size)

   完了条件: 自動回収を前提にした説明を修正し、05を実装する際は実際の cache の寿命・サイズ制御に対応した説明を用意する。refresher の呼び出し回数だけでは、SDKで設定が更新されることまでは検証できない。

8. **[P2] HTTP でのテナント認可を省いていることが利用者向けに明示されていない。**

   対象: `src/mtappconfig/webapp.py:111`、README のセキュリティ説明。

   同一の未認証クライアントから tenant-a と tenant-b の API がどちらも200になることを確認しました。比較用UIとしては自然ですが、レジストリ照合は存在確認と入力検証であり、要求元にそのテナントを読む権利があるかの認可ではありません。「テナントID検証が最も重要」「テナント間で値を漏らさない」といった説明だけでは、読者がHTTP認可も実装済みと誤解し得ます。

   完了条件: このアプリは全サンプル設定を閲覧するデモで、ユーザー認証・テナント所属の認可を実装しないことを README と画面に明記する。実アプリでは認証済みコンテキストからテナントを決定することを説明する。この目的のために認証基盤の実装まで追加する必要はない。

9. **[P2] README を順に実行する導線が途切れる。**

   対象: `README.md:172`, `:183`、`samples/04-snapshot-references/README.md:62`, `:121`。

   ルート手順で sample 01 に `cd` した後、SDKインストールは存在しないローカルの `requirements-azure.txt` を参照します。`../../requirements-azure.txt` または明示的なルート復帰が必要です。sample 04 の起動例にも前提となる作業ディレクトリがありません。ロールバックの Python スニペットは新しいフェイクストアを作って参照を書き換えるだけで、起動済み Flask のストアには影響せず、変更前後の値も表示しません。

   完了条件: 各コードブロックの作業ディレクトリを明示し、rollout demo は同じプロセスの store/source/config を使って変更前→refresh→変更後を表示する、コピーして実行可能な手順にする。

追加で説明を整えるとよい点があります。sample 04 の Azure 作成手順は `tenant-a/*` 全件を snapshot にしますが、フェイクの seed はプレフィックスなしの部分集合を使います。通常の出力は一致しても「snapshotにないキーのフォールバック」の証拠が同一ではありません。また Azure 手順には tenant-b の missing reference がなく、LogLevel/BetaDashboard の直接値を旧値へ戻さないため、参照が失われた際の tenant-a のフォールバックもフェイクと異なります。データセットを統一するか、差異と期待値を明記してください。snapshot 作成後に通常キーを元へ戻せないかのような説明も修正が必要です。

全記事変更をすべて Azure 上に実装する必要はありません。geo-replication の別クォータ、Developer の SLA、CMK の SKU 条件、Front Door の公開性・refresh 制約は、一次資料との照合を検証方法として選べます。ただし、実装済みの欄と混ぜないことが必要です。今回、[geo-replication](https://learn.microsoft.com/en-us/azure/azure-app-configuration/howto-geo-replication)、[FAQ](https://learn.microsoft.com/en-us/azure/azure-app-configuration/faq)、[CMK要件](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-customer-managed-keys#requirements)、[Front Doorの公開性](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-hyperscale-client-configuration#security)、[sentinel refresh非対応](https://learn.microsoft.com/en-us/azure/azure-app-configuration/how-to-load-azure-front-door-configuration-provider#configuration-doesnt-refresh)を照合し、該当する追記の方向性は支持できます。

まず検証対象と証拠の対応表を固定し、P1の保証・証拠の不足を解消することを推奨します。CIにはオフラインのテストとIaCコンパイルを載せ、実Azureの検証結果は別に記録すれば、第三者が結論の根拠と限界を追跡できます。
