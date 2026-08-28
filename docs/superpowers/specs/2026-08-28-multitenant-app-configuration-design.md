# マルチテナント × Azure App Configuration サンプル 設計書

- 作成日: 2026-08-28
- 対象ドキュメント: [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration)
- 評価軸: [Azure Well-Architected Framework](https://learn.microsoft.com/en-us/azure/well-architected/)

## 1. 目的とスコープ

Microsoft Learn の「Multitenancy and Azure App Configuration」に記載された分離モデルと機能を、
**動かして違いが分かる Python / Flask のサンプル**として実装する。

記事が扱うパターンを漏れなくカバーする:

| 記事の項目 | 本リポジトリでの扱い |
| --- | --- |
| Shared store（キープレフィックス） | サンプル `01-shared-store-key-prefix` |
| Shared store（ラベル） | サンプル `02-shared-store-label` |
| Store per tenant | サンプル `03-store-per-tenant` |
| Shared settings / Tenant-specific settings | 3サンプル共通（コアで両方をマージ） |
| Application-side caching | 3サンプル共通（コアの TTL + LRU キャッシュ） |

### スコープ外

- Flask アプリ自体の Azure へのデプロイ（Container Apps / App Service）。Bicep はリソース作成までとする。
- 認証・認可を伴う実際のテナントログイン。テナントは URL パスで指定する。
- Feature Management ライブラリによる本格的なフィーチャーフラグ運用。設定値としての真偽値までとする。

## 2. 前提

- Python 3.14.3。`.python-version` でローカルに固定しているが、**このファイルは git 管理しない**（利用者の判断）。したがってリポジトリ側の下限は `pyproject.toml` の `requires-python` のみが担保する。
- パッケージ管理は `uv`
- Azure 認証は `DefaultAzureCredential` のみ。接続文字列は使わない。

### 依存関係の構成

開発環境はセキュリティ上の制約で `files.pythonhosted.org` に到達できず、
パッケージは uv のローカルキャッシュにあるものだけが利用可能である。
検証の結果、可否は以下のとおり。

| パッケージ | オフライン取得 |
| --- | --- |
| `flask`, `pytest`, `azure-identity`, `azure-core`, `cryptography`, `msal` | 可 |
| `azure-appconfiguration`, `azure-appconfiguration-provider` | **不可** |

したがって依存を分割し、**基本インストールが App Configuration の SDK なしで完結する**
構成とする。これは「ローカルフェイクだけで全機能が動作し、全テストが通る」という要件を
パッケージ構成のレベルでも保証する。

```toml
[project]
dependencies = ["flask"]

[dependency-groups]
dev = ["pytest"]

# Not a distributable package: hatchling is not obtainable offline either.
[tool.uv]
package = false
```

Azure SDK は `pyproject.toml` に**書かない**。`uv lock` は optional-dependencies も
解決対象に含めるため、そこへ書くと取得不能なパッケージのせいで `uv sync` 自体が失敗し、
フェイクだけで動かしたい利用者まで巻き込む。SDK は `requirements-azure.txt` に分離し、
実 Azure に繋ぐときだけ個別にインストールする。また、この環境では uv コマンドに
`--offline` が必要（`files.pythonhosted.org` へ到達できないため）。

### 既知の制約

`azure_source.py`（実 provider への束縛）は、上記の理由により**この環境では実行も import も
できず、未検証のまま残る**。影響を1ファイルに閉じ込めるため、次の措置を取る。

- `azure_source.py` 内の `azure.*` の import は関数内で行う遅延 import とし、
  SDK 未インストールでもモジュールの import が壊れないようにする。
  SDK が無い状態で実 Azure 経路を使おうとした場合は、原因が分かるエラーを送出する。
- 実 Azure に対するテストは `@pytest.mark.live` の下に隔離し、既定でスキップする。
- コア・3パターンの実装・全テストは Azure SDK なしで動作・検証できることを受け入れ条件とする。

## 3. アーキテクチャ

### 3.1 ディレクトリ構成

```
README.md                       # 入口。3パターンの比較表、WAF の観点、選び方
pyproject.toml
.python-version                 # 3.14.3
src/mtappconfig/                # 共通コア
  tenants.py                    # テナントレジストリ + テナント ID 検証
  source.py                     # TenantConfigSource プロトコル / TenantConfig
  cache.py                      # TTL + LRU のテナント別キャッシュ
  fake.py                       # ローカルフェイク App Configuration ストア
  azure_source.py               # provider load() ラッパ（DefaultAzureCredential）
  observability.py              # tenant_id 付き構造化ログ、キャッシュ統計
  webapp.py                     # create_app(source) — Flask app factory
samples/
  01-shared-store-key-prefix/   README.md, source_key_prefix.py, app.py, seed_key_prefix.py, main.bicep, tests/
  02-shared-store-label/        同上（source_label.py / seed_label.py）
  03-store-per-tenant/          同上（source_store_per_tenant.py / seed_store_per_tenant.py）
                                ※ モジュール名をサンプルごとに一意にするのは、pytest が3サンプルを
                                  1セッションで収集する際の sys.modules 衝突を避けるため。
infra/modules/                  # 共通 Bicep モジュール（appconfig / rbac / monitoring）
tests/                          # コアのテスト
docs/superpowers/specs/         # 本設計書
```

各サンプルの `app.py` は実質3行（`create_app(<そのパターンの Source>)`）。
**パターン間の差分は `source.py` に集約する。** 3本を diff したときにパターンの違いだけが
浮かび上がることが、この構成の狙いである。

### 3.2 中心の抽象

抽象はひとつだけに絞る。

```python
class TenantConfigSource(Protocol):
    def load(self, tenant_id: str) -> TenantConfig: ...

@dataclass
class TenantConfig:
    tenant_id: str
    values: Mapping[str, Any]
    refresh: Callable[[], bool]   # 変更を検知したら True
```

`TenantConfigSource` の実装は3種（キープレフィックス / ラベル / ストア別）× 2バックエンド
（ローカルフェイク / 実 Azure）。バックエンドは環境変数で切り替え、未設定ならフェイクを使う。

- サンプル01・02（共有ストア1つ）: `APPCONFIG_ENDPOINT` にストアのエンドポイントを設定
- サンプル03（テナント別ストア）: `APPCONFIG_ENDPOINTS` に
  `{"tenant-a": "https://...azconfig.io", "tenant-b": "..."}` の JSON を設定

サンプル03 では、レジストリに存在するがエンドポイントが未登録のテナントは設定エラーとして
起動時に検出する（リクエスト時に初めて失敗する状態を作らない）。

### 3.3 リクエストのデータフロー

```
GET /t/<tenant_id>/
  1. テナント ID 検証（レジストリ照合 + 正規表現）
       → 不正なら 404。検証を通らない ID は一切ストアに到達しない。
  2. cache.get(tenant_id)
       hit  → entry.refresh()      # refresh_interval 内なら no-op
       miss → source.load(tenant_id) → キャッシュ格納（LRU 追い出し + TTL）
  3. 共有設定 + テナント設定をマージ（テナント側が優先）
  4. レンダリング
```

### 3.4 アプリ側キャッシュの設計

記事は .NET の `IMemoryCache` にテナント ID をキーとして `IConfiguration` を保持する方針を示す。
Python では以下で等価な性質を得る。

- テナントごとに独立した provider インスタンスを保持する `dict`
- **LRU 追い出し**（`max_entries`）: 記事の「メモリ逼迫時に未使用インスタンスを退避できる」に対応
- **TTL**（`ttl_seconds`）: 記事の「テナントごとに有効期限を設定できる」に対応
- **遅延ロード**: 全テナント一括ロードはしない。記事が指摘するとおり、テナント数の増加に伴う
  ロード時間とメモリ消費の増大を避ける。

重要な実装上の差分として、**Python の provider はバックグラウンドで自動更新しない**。
`refresh_enabled=True` を指定したうえで、リクエスト契機で `config.refresh()` を呼ぶ
activity-driven refresh になる。`refresh_interval` 未経過の `refresh()` は no-op なので、
毎リクエストで呼んでもストアへの負荷にはならない。この挙動はサンプルで明示的に見せる。

### 3.5 設定キーの構成

記事の「shared settings」と「tenant-specific settings」を両方扱う。

| 種別 | キー例 | 用途 |
| --- | --- | --- |
| 共有 | `App:SupportEmail`, `App:Version` | 全テナント共通。更新箇所を1つに保つ |
| テナント固有 | `DisplayName`, `LogLevel`, `DatabaseName`, `Features:BetaDashboard` | 記事が例示する用途（テナント別ログレベル、DB 名） |

### 3.6 HTTP エンドポイント

| メソッド / パス | 用途 |
| --- | --- |
| `GET /` | テナント一覧と、稼働中のパターン名 |
| `GET /t/<tenant_id>/` | 解決後の設定を HTML で表示 |
| `GET /t/<tenant_id>/api/config` | 同じ内容を JSON で返す |
| `GET /healthz` | 生存確認（依存先を叩かない） |
| `GET /readyz` | 設定ストアへの到達性を含む準備確認 |
| `GET /_diagnostics/cache` | キャッシュ統計（hit / miss / evict / エントリ数） |

`/_diagnostics/cache` は、記事のキャッシュ論をその場で観察するための窓として置く。

## 4. 各サンプルの実装差分

| | 01 key-prefix | 02 label | 03 store-per-tenant |
| --- | --- | --- | --- |
| ストア数 | 共有1つ | 共有1つ | テナント数だけ |
| 選択方法 | `SettingSelector(key_filter=f"{tid}/*")` + `trim_prefixes=[f"{tid}/"]` | `SettingSelector(label_filter=tid)` | テナント → エンドポイントのマップを引き、ストアごとに `load()` |
| ラベルの空き | 環境（dev/prod）等に使える | テナントで占有され他に使えない | 環境等に使える |
| データ分離 | 低 | 低 | 高 |
| 性能分離 | 低 | 低 | 高 |
| デプロイ/運用の複雑さ | 低 | 低 | 中〜高 |
| コスト | 低 | 低 | 中〜高 |
| Bicep | ストア1 + ロール割当 | ストア1 + ロール割当 | ストアをループ生成 + ストアごとロール割当 |

各サンプルの README には、上記トレードオフに加えて「いつこれを選ぶか」を書く。

- **01**: 記事が既定として推奨するパターン。共有アプリ層を持つ大規模マルチテナントに適する。
- **02**: 記事の指摘を明記する — **テナント識別にラベルを使うとラベルを他の用途（バージョニング、
  環境）に使えなくなるため、通常はキープレフィックスを推奨**。テナント別にアプリをデプロイして
  いる場合には有効。なお provider の既定はラベルなしの設定のみを読むため、
  ラベル指定が必須になる点も示す。
- **03**: 記事が挙げる採用理由に沿う — テナントごとに異なる顧客管理キー（CMK）が必要な場合、
  またはテナントが設定データの分離を要求する場合。アクセス権限はストア単位でしか制御できない
  ため、権限を分けるにはストアを分ける必要がある。

## 5. Well-Architected Framework の適用

README で語るだけでなく、コードで示すものを列挙する。

### セキュリティ

- **Entra ID 認証のみ**: `DefaultAzureCredential` を使い、接続文字列とアクセスキーは使わない。
  Bicep では `disableLocalAuth: true` を設定する。
- **最小権限の RBAC**: アプリのマネージド ID に **App Configuration Data Reader**
  （ロール定義 ID `516239f1-63e1-4d78-a4de-a74fb236a071`）のみを割り当てる。
- **テナント ID 検証（本サンプルの最重要ポイント）**: URL 由来のテナント ID を検証せずに
  `key_filter` へ渡すと、`*` などの指定によりクロステナントの情報漏洩が起きる。
  レジストリ照合と厳格な正規表現（`\A[a-z0-9][a-z0-9-]{1,30}[a-z0-9]\Z`、すなわち英小文字・
  数字・ハイフンのみで3〜32文字、先頭末尾は英数字）を通過した ID だけがストアに到達する
  設計とし、テストで担保する。レジストリ照合だけでも防げるが、二重の防御として両方を課す。
  アンカーに `^`/`$` を使ってはならない。Python の `$` は末尾の改行の直前にもマッチするため、
  `"tenant-a\n"` がパターンを通過してしまう。`\A`/`\Z` を使う。
- **シークレットを置かない**: 機密値は App Configuration ではなく Key Vault に置き、
  Key Vault 参照として保存する。**これは README に運用指針として記載するに留め、実装しない。**
  ローカルフェイクで `secret_resolver` 相当を模倣しても、実物の挙動を確認できない環境では
  その模倣が正しい保証が得られず、「これが正解」として残るコードになるため。

### 信頼性

- **一時的障害への対応**: SDK 標準のリトライと `startup_timeout` を明示的に設定する。
- **キャッシュによる劣化運転**: 設定ストアが到達不能でも、キャッシュ済みの設定でリクエストを
  処理し続ける。`on_refresh_error` はログ記録のみを行い、リクエストを失敗させない。
- **健全性の分離**: `/healthz`（依存先を叩かない）と `/readyz`（ストア到達性を含む）を分ける。

### パフォーマンス効率

- テナント別の遅延ロード（全テナント一括ロードをしない）
- TTL + LRU による有界なメモリ使用
- activity-driven refresh により、毎リクエストでストアを叩かない

### オペレーショナルエクセレンス

- Bicep による IaC（`infra/modules/` を各サンプルの `main.bicep` が合成する）
- すべてのログに `tenant_id` を付与する構造化ログ
- 診断設定を Log Analytics に送出

### コスト最適化

README に tier ごとの制約表を置き、なぜ共有ストアが既定の推奨なのかをコスト面から読めるようにする。

| | Free | Developer | Standard | Premium |
| --- | --- | --- | --- | --- |
| ストア数 | 3 / リージョン / サブスクリプション | 無制限 | 無制限 | 無制限 |
| リクエスト | 1,000 / 日 | 6,000 / 時 | 30,000 / 時 | 制限なし |
| ストレージ | 10 MB | 500 MB | 1 GB | 4 GB |

（出典: [Azure subscription and service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-app-configuration)）

この表から導かれる実用的な帰結をサンプル03の README に明記する:
**Free tier ではストアが1リージョン・1サブスクリプションあたり3つまでで、この方式は共有ストアも
1つ使うため、Free では2テナントまでしか試せない。** 記事が「Standard tier ならストアを無制限に作れる」と述べているのはこの制約に対応する。
また共有ストア方式では、テナント数の増加に伴い1ストアのリクエスト/時とストレージの上限に
到達しうるため、複数の共有ストアへテナントを分散する選択肢がある旨も記載する。

### トレードオフ

セクション4の比較表をそのまま WAF のトレードオフ表として README に掲載し、
「データ分離・性能分離を上げるとデプロイ/運用の複雑さとコストが上がる」という
記事の主張を数値と構成の両面から追えるようにする。

## 6. テスト方針

`pytest` を使い、**すべてのテストがフェイクストアに対して Azure 不要で実行できる**ことを要件とする。
実装は TDD で進める。

| 分類 | 内容 |
| --- | --- |
| 契約テスト | 同一のテナント設定を投入したとき、3パターンすべてが同一の解決結果を返すこと。パターンが差し替え可能であることを保証し、共通コア構成の妥当性を裏付ける |
| セキュリティ | 不正なテナント ID（`*`、`a/../b`、`../`、空文字、未登録 ID）が拒否されること。テナント A のリクエストでテナント B の値が露出しないこと |
| キャッシュ | TTL 失効、LRU 追い出し、`refresh_interval` 未経過時の no-op、リフレッシュ失敗時にキャッシュ値で継続すること |
| 設定マージ | テナント設定が共有設定を上書きすること。テナント固有キーがないときに共有設定へフォールバックすること |
| HTTP | 各エンドポイントのステータスコードとレスポンス形状 |

実 Azure に対するテストは `@pytest.mark.live` を付けてオプトインとし、既定ではスキップする。

## 7. 実装順序

1. `pyproject.toml` の作成と、`azure` エクストラ抜きでの `uv sync` の成功確認
2. コア: `tenants.py`（検証） → `source.py` → `fake.py` → `cache.py` → `webapp.py`
3. サンプル01（キープレフィックス）: フェイク → 契約テスト → Bicep → README
4. サンプル02（ラベル）
5. サンプル03（テナント別ストア）
6. `azure_source.py`（遅延 import、未検証）と `live` マークのテスト
7. ルート README（比較表、WAF の観点、選び方）
