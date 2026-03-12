# NADJA Process Intelligence Platform — WEBアプリ設計指示書

作成日: 2026-03-11
作成者: NADJA Corporation

※プロトタイプ開発後に修正予定

---

## 1. この文書の目的

本文書は、NADJA Process Intelligence Platform の WEBアプリケーション（MVP版）の設計を行うための指示書である。設計者（人間またはAI）がこの文書を読めば、何を設計すべきか・どこまでが対象か・何が既に決まっているかを理解できることを目的とする。

---

## 2. プロジェクト全体像

### 2.1 ビジネス目的

NADJAはクライアント企業へのAIエージェント導入効果を定量的に可視化するプロセスマイニング基盤を自社構築している。Microsoft Power Automate Process Mining（月額約40万円）と同等機能を、OSSスタック（pm4py + PostgreSQL + Metabase/Streamlit）で実現し、さらに「AIエージェント効率測定」という独自の付加価値を載せる。

### 2.2 システム全体の構成（4層アーキテクチャ）

```
Layer 1: データ収集層
  ├── クライアント業務システムの監査ログ自動取得（PostgreSQL/SAP/M365等）
  ├── タスクマイニングツール（ProcessMiningTest） ← 開発中
  └── CSVファイル手動アップロード

Layer 2: ストレージ層
  └── PostgreSQL

Layer 3: 分析エンジン層
  └── FastAPI + pm4py

Layer 4: 可視化層
  └── Streamlit（MVP）/ Metabase（将来）
```

### 2.3 今回の設計対象

**Layer 2〜4 の MVP（Minimum Viable Product）版を設計する。**

Layer 1 のタスクマイニングツール（ProcessMiningTest）は別途C#で開発中であり、CSVファイルを出力する。WEBアプリはこのCSVを入力として受け取る。

---

## 3. 既に存在するもの（前提条件）

### 3.1 ProcessMiningTest（C# WinFormsアプリ、開発中）

事務職PCで操作ログを収集し、プロセスマイニング用CSVを出力するデスクトップツール。

> **⚠️ CSVフォーマットは変更される可能性がある。** 現在2つのアプローチ（L1: プロセス名のみ / L2: ボタン名・フィールド名あり）を並行検証中であり、検証結果によってカラム構成が変わる。WEBアプリ側はカラム追加に耐えうる柔軟な設計にすること（§5.1 参照）。

**現行CSVフォーマット（L1版、変更の可能性あり）：**

```csv
CaseID,Activity,Timestamp,Duration,Source
20260310_001,Gmail,2026-03-10 10:00:32,120,Browser
20260310_001,Google Sheets,2026-03-10 10:02:32,95,Browser
20260310_001,Slack,2026-03-10 10:04:07,180,Browser
20260310_002,Excel作業,2026-03-10 10:15:00,600,Window
20260310_002,Gmail,2026-03-10 10:25:00,180,Browser
```

**必須カラム（変わらない）：**

| カラム | 型 | 説明 |
|--------|-----|------|
| CaseID | string | 業務セッションID。`yyyyMMdd_NNN` 形式。日付+連番 |
| Activity | string | アクティビティ名。サービス名（Gmail, Slack等）またはアプリ名（Excel作業, Teams等） |
| Timestamp | datetime | イベント開始日時（ローカルタイム） |

プロセスマイニングの3要素（CaseID, Activity, Timestamp）は確定。これ以外のカラムは追加・変更の可能性がある。

**現行の追加カラム（変更の可能性あり）：**

| カラム | 型 | 説明 | 変更可能性 |
|--------|-----|------|-----------|
| Duration | int | 滞在時間（秒） | L2版では1操作=1行になり、意味が変わる可能性 |
| Source | string | `Browser` / `Window` / `UIActivity`（新プロトタイプ） | 値の種類が増える可能性 |

**L2版で追加が見込まれるカラム：**

| カラム | 型 | 説明 | 例 |
|--------|-----|------|-----|
| ElementName | string | 操作対象のUI要素名（ボタン名、タブ名等） | "挿入", "保存", "Sheet2" |
| ElementType | string | UI要素の種類 | "Button", "TabItem", "Edit" |
| EventType | string | イベントの種類 | "WindowChange", "FocusChange" |
| ResourceType | string | 実行者の種類 | `human` 固定 |

**L1版 vs L2版のActivity粒度の違い：**

```
L1版: Activity = "Excel作業"（1アプリ滞在 = 1行）
L2版: Activity = "Excel: セル編集" → "Excel: 挿入タブ" → "Excel: 表ボタン"（1操作 = 1行）
```

**ProcessMiningTestの処理フロー：**

```
ブラウザ履歴収集（Chrome/Edge/Firefox）
  → BrowserSanitizer（タイトル抽象化、URLパス削除、PII除去）
  → ProcessMiningConverter（セッション分割、CaseID付与、滞在時間算出）
  → CSV出力

アクティブウィンドウ追跡 ← 2つのアプローチを検証中
  方式A（L1）: GetForegroundWindow ポーリング → プロセス名のみ記録
  方式B（L2）: SetWinEventHook + UI Automation → ボタン名・フィールド名まで記録
```

**プライバシー設計：**
- L1: タイムスタンプ + サービス名/アプリ名 + 滞在時間のみ。PIIリスクゼロ
- L2: L1 + ボタン名・フィールド名。入力値（パスワード等）は記録しない。フィールド名に業務情報が含まれうるため、サニタイズ層（ホワイトリスト方式）で保護

### 3.2 初期設計書（存在するが未実装）

PostgreSQLスキーマ（12テーブル）、FastAPI（14エンドポイント）、Airflow DAG、Metabase/Streamlitダッシュボード等のフルスタック設計が存在する。今回のMVPではこの一部のみを実装する。

### 3.3 プライバシー・セキュリティ方針書（存在する）

7つの観点（プライバシー、セキュリティ、法的、倫理的、技術的健全性、運用管理、データ品質）からの分析と対策方針が文書化済み。WEBアプリ設計でもこの方針に準拠する必要がある。

### 3.4 UI Activity Monitor プロトタイプ（検証中）

ProcessMiningTest の L1 アプローチ（プロセス名のみ）とは別に、SetWinEventHook + UI Automation を使ったL2プロトタイプを検証中。ボタン名・フィールド名レベルの操作記録を取得できるか確認している。

**検証結果によって以下が変わる可能性がある：**
- CSVのカラム構成（ElementName, ElementType 等の追加）
- Activity名の粒度（「Excel作業」→「Excel: セル編集」等）
- 1行の意味（L1: 1アプリ滞在=1行 → L2: 1操作=1行。行数が大幅に増加）
- データ量（L2版はL1の10〜100倍のレコード数になりうる）

**WEBアプリ設計への影響：** CSVインポート層を柔軟に設計すれば、L1/L2どちらが採用されても対応可能（§5.1 参照）。分析エンジン（pm4py）は CaseID, Activity, Timestamp の3カラムさえあれば動作するため、追加カラムは表示・フィルタ用途として `event_attrs JSONB` に格納する。

---

## 4. MVP で設計すべき範囲

### 4.1 MVPの定義

**「ProcessMiningTestが出力したCSVをアップロードし、プロセスマップと基本KPIを表示できる」**

これにより、クライアントに「業務の可視化」をデモできる状態を最短で作る。

### 4.2 MVPに含めるもの

| 機能 | 概要 |
|------|------|
| CSVアップロード | ProcessMiningTestのCSVを取り込み、PostgreSQLに格納 |
| プロセスマップ表示 | DFG（Directly-Follows Graph）をインタラクティブに可視化 |
| バリアント一覧 | 業務パターンの種類と頻度を表示 |
| 基本KPI表示 | ケース数、平均所要時間、アクティビティ別統計等 |

### 4.3 MVPに含めないもの（将来フェーズ）

| 機能 | 理由 |
|------|------|
| AIエージェント効率測定（automation_kpi, agent_execution_log） | Phase 3以降。Before データがまだない |
| Before/After比較ダッシュボード（baseline_snapshot） | Phase 3以降 |
| OCEL対応（business_object, event_object） | 高度な分析機能。MVP後 |
| 監査ログ自動取得（Airflow DAG、各種コネクタ） | Layer 1の別プロジェクト |
| 適合性チェック（Token Replay / Alignment） | 参照モデルが必要。MVP後 |
| 自然言語分析（LLM連携） | Phase 2以降 |
| Metabase連携 | Streamlitで十分な段階では不要 |
| マルチテナント / 認証認可 | 単一ユーザーで開始、後から追加 |

---

## 5. 設計すべき項目

以下の項目について、具体的な設計書を作成すること。

### 5.1 CSVインポート仕様

ProcessMiningTestのCSV → PostgreSQL `event` テーブルへのカラムマッピングを定義する。

> **⚠️ 最重要設計方針：CSVカラム変更に耐える柔軟な設計にすること。**
>
> CSVのカラム構成は L1/L2 の検証結果によって変わる。以下の3原則を守ること：
>
> 1. **必須3カラム（CaseID, Activity, Timestamp）のみ固定カラムにマッピング。** この3つはプロセスマイニングの基本であり、変わらない。
> 2. **それ以外のカラムは `event_attrs JSONB` に格納。** Duration, Source, ElementName, ElementType 等はすべて JSONB に入れる。カラムが増えてもスキーマ変更が不要。
> 3. **マッピング定義をコードにハードコードしない。** CSV→DBの変換ルールを設定ファイルまたはDB上のマッピングテーブルで管理し、カラム追加時はマッピング定義の追加だけで対応。

**設計で決めること：**

- 上記3原則に基づく具体的なマッピング設計
- CSVヘッダー行を読み取って動的にカラムを検出する仕組み
- 必須3カラムが欠けている場合のエラー処理
- `Duration` → `end_timestamp` に変換するか、`event_attrs` にそのまま保持するか
- `process_id` の付与方法（アップロード時にユーザーが選択 or 自動生成）
- `case_instance` テーブルへの自動集計方法（ケース開始/終了日時、アクティビティ数等）
- CSVバリデーションルール（必須カラム、日付フォーマット、重複チェック等）
- 同一プロセスに複数回CSVをアップロードした場合の挙動（追記 or 上書き）
- L2版でレコード数が10〜100倍になった場合のパフォーマンス考慮（バッチインサート、インデックス戦略等）

### 5.2 データベーススキーマ（MVP版）

初期設計の12テーブルから、MVPに必要な最小セットを抽出する。

**MVP必須テーブル（想定）：**

| テーブル | 役割 |
|---------|------|
| `event` | イベントファクト（中心テーブル） |
| `process_definition` | プロセス定義（O2C, P2P等の分析対象定義） |
| `case_instance` | ケースディメンション（自動集計） |

**設計で決めること：**

- 初期設計の `event` テーブル（18カラム）からMVPで必要なカラムの選定
- MVP不要カラム（`agent_id`, `is_automated`, `activity_cost` 等）の扱い（NULLable で残す or 削除）
- 将来のテーブル追加を見据えたマイグレーション戦略
- インデックス設計（初期設計のインデックス戦略をMVP用に絞り込む）

**参考：初期設計の `event` テーブル定義**

```sql
CREATE TABLE event (
  event_id BIGSERIAL PRIMARY KEY,
  case_id VARCHAR(255) NOT NULL,
  activity_name VARCHAR(255) NOT NULL,
  event_timestamp TIMESTAMPTZ NOT NULL,
  end_timestamp TIMESTAMPTZ,
  resource_id VARCHAR(255),
  resource_type VARCHAR(50),
  process_id INT NOT NULL,
  activity_cost NUMERIC(12,2),
  lifecycle VARCHAR(50) DEFAULT 'complete',
  is_automated BOOLEAN DEFAULT FALSE,
  agent_id VARCHAR(255),
  source_system VARCHAR(100),
  has_error BOOLEAN DEFAULT FALSE,
  is_rework BOOLEAN DEFAULT FALSE,
  event_attrs JSONB
);
```

### 5.3 API設計（FastAPI、MVP版）

pm4pyをバックエンドに持つREST APIを設計する。

**MVP必須エンドポイント（想定4本）：**

| メソッド | パス | 機能 |
|---------|------|------|
| POST | `/api/v1/upload/csv` | CSVアップロード→DB格納 |
| POST | `/api/v1/discover/dfg` | DFG生成（プロセスマップの元データ） |
| GET | `/api/v1/variants` | バリアント一覧 + 頻度 |
| GET | `/api/v1/kpi/summary` | 基本KPI |

**設計で決めること：**

- 各エンドポイントのリクエスト/レスポンスの具体的なJSON構造
- DFGのレスポンス形式（ノード一覧 + エッジ一覧 + 頻度 + 平均時間）
- KPIの具体的な算出項目と計算式
- フィルタリング（日付範囲、プロセスID、Source種別等）
- エラーハンドリング（CSV不正、データなし等）
- pm4pyとの連携方法（DataFrameの組み立て方、メソッド呼び出し）

**基本KPIとして返す項目（想定）：**

| KPI | 計算方法 | 意味 |
|-----|---------|------|
| ケース数 | CaseIDのユニーク数 | 業務セッション数 |
| 平均ケース所要時間 | 各CaseIDの(最終Timestamp - 最初Timestamp)の平均 | 1セッションの平均長さ |
| ケースあたり平均アクティビティ数 | 各CaseID内のレコード数の平均 | 1セッションでの平均操作回数 |
| アクティビティ別実行回数 | Activityごとのcount | 各アプリ/サービスの利用頻度 |
| アクティビティ別平均滞在時間 | Activityごとの平均Duration | 各アプリ/サービスの平均利用時間 |
| アクティビティ別合計時間 | Activityごとの合計Duration | 時間配分 |
| バリアント数 | CaseID内のActivity列パターンの種類数 | 業務パターンの多様性 |
| トップバリアントカバー率 | 最頻バリアントの件数 / 全ケース数 | 業務の標準化度合い |

### 5.4 フロントエンド設計（Streamlit、MVP版）

**MVP画面構成（3ページ）：**

| # | ページ | 主要コンテンツ |
|---|--------|--------------|
| 1 | CSVアップロード | ファイルアップロード、プロセス名入力、取り込み結果表示 |
| 2 | プロセスマップ | DFG可視化（ノード=アクティビティ、エッジ=遷移、太さ=頻度）、バリアント選択フィルタ |
| 3 | KPIダッシュボード | 基本KPIカード群、アクティビティ別の棒グラフ/円グラフ、時系列推移 |

**設計で決めること：**

- 各ページのワイヤーフレーム（レイアウト、UI部品の配置）
- DFGの描画方法（Graphviz / Plotly / NetworkX / カスタムD3.js）
- フィルタリングUI（日付範囲、Source種別、アクティビティ選択等）
- Streamlit ↔ FastAPI の通信方法（直接 requests or Streamlit のセッション管理）
- pm4pyのビジュアライゼーションをStreamlitでどう表示するか

### 5.5 インフラ設計（Docker Compose、MVP版）

**MVP構成（3コンテナ）：**

```yaml
services:
  postgres:    # PostgreSQL 16
  api:         # FastAPI + pm4py + Uvicorn
  streamlit:   # Streamlit（フロントエンド）
```

**設計で決めること：**

- 各コンテナのDockerfile構成
- PostgreSQLの初期化スクリプト（スキーマ作成）
- ボリュームマウント（DBデータの永続化）
- ネットワーク設計（コンテナ間通信）
- 環境変数による設定管理（DB接続文字列等）
- 開発環境と本番環境の差分（VPSデプロイ時の追加構成）
- ヘルスチェック

### 5.6 初期設計との対応表

初期設計の各要素が、MVPでどう扱われるかを明示する表を作成すること。

| 初期設計の要素 | MVP での扱い | 理由 |
|---------------|-------------|------|
| `event` テーブル | ✅ 実装（カラム削減） | 核心テーブル |
| `automation_agent` テーブル | ❌ 後回し | AI計測はPhase 3 |
| ... | ... | ... |

---

## 6. 技術的制約・決定事項

### 6.1 確定している技術選定

| 要素 | 技術 | 理由 |
|------|------|------|
| DB | PostgreSQL 16 | 初期設計で確定済み。JSONB対応、信頼性 |
| 分析エンジン | pm4py 2.7+ | 初期設計で確定済み。IEEE準拠、OCEL対応 |
| API | FastAPI + Uvicorn | 初期設計で確定済み。OpenAPI自動生成 |
| フロントエンド（MVP） | Streamlit | pm4pyとPythonネイティブ連携 |
| コンテナ | Docker + Docker Compose | 開発・本番環境の統一 |

### 6.2 セキュリティ要件（MVP最小限）

- PostgreSQL接続は環境変数で管理（ハードコードしない）
- CSVアップロード時のファイルサイズ上限設定
- SQLインジェクション対策（SQLAlchemy のパラメータバインディング使用）
- MVP段階では認証なし（ローカルまたはVPN内での使用を前提）

### 6.3 将来への拡張ポイント

MVPの設計時に、以下の拡張を意識した設計にすること（実装はしない）：

- `resource` テーブルの追加（human / ai_agent / rpa_bot の区別）
- `automation_kpi` テーブルの追加（Before/After比較）
- Airflow DAGによるETL自動化
- JWT認証の追加
- マルチテナント対応（`client_id` によるデータ分離）
- Metabaseの導入（Streamlit と並行運用）
- L2データ（ボタン名・フィールド名）対応時の `event_attrs JSONB` インデックス追加（GINインデックス）
- L2データのレコード量増大（L1の10〜100倍）に備えた `event` テーブルの月次パーティショニング（初期設計 §3.5 参照）
- UI Activity Monitor プロトタイプの統合（Source="UIActivity" のCSVインポート対応）

---

## 7. 成果物

設計者は以下の成果物を作成すること。

| # | 成果物 | 形式 |
|---|--------|------|
| 1 | CSVインポート仕様書（カラムマッピング表、バリデーションルール） | Markdown |
| 2 | MVPスキーマDDL（CREATE TABLE文 + インデックス） | SQL |
| 3 | API仕様書（各エンドポイントのリクエスト/レスポンスJSON例） | Markdown |
| 4 | Streamlit画面設計（ワイヤーフレームまたは画面構成図） | Markdown + 図 |
| 5 | Docker Compose設計（docker-compose.yml + 各Dockerfile構成） | YAML + Markdown |
| 6 | 初期設計との対応表（MVPで何を作り、何を後回しにしたか） | Markdown |
| 7 | 実装順序と検証方法 | Markdown |

---

## 8. 参考資料

本設計に必要な既存ドキュメント一覧：

| ドキュメント | 内容 | 参照すべき箇所 |
|-------------|------|---------------|
| `プロセスマイニング初期設計.md` | フルスタック設計書 | §3 スキーマ設計、§4 分析エンジン設計、§7 ダッシュボード設計 |
| `PRIVACY_SECURITY_POLICY.md` | プライバシー・セキュリティ方針 | §3 プライバシー対策、§4 セキュリティ対策 |
| `L1_ProcessMining_Design.md` | L1プライバシーレベル設計 | CSV出力フォーマット、セッション分割ロジック |
| `ActiveWindowTracking_Design.md` | アクティブウィンドウ追跡設計（L1版） | ProcessMiningMerger、統一CaseID、Source="Window" |
| `DESIGN.md` | UI Activity Monitor 設計（L2プロトタイプ） | SetWinEventHook + UI Automation 方式、ActivityRecord 構造、§6 統合時のマッピング案 |
| `VERIFICATION_GUIDE.md` | UI Activity Monitor 検証手順 | アプリ別テストシナリオ、評価基準（A〜Eレベル） |
| ProcessMiningTest ソースコード | C# WinFormsアプリ | ProcessMiningEntry.cs、ProcessMiningConverter.cs、CsvExporter.cs |
