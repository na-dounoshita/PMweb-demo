# NADJA PM WebApp プロトタイプ設計書

## ゴール

ProcessMiningTestが出力したCSVをアップロードし、プロセスマップと基本KPIを表示するWebアプリのプロトタイプを作る。`docker-compose up` で起動し、ブラウザで操作できる状態がゴール。

---

## 入力CSV仕様

ProcessMiningTest（C# WinFormsアプリ）が出力するCSV。UTF-8 BOM付き。

```csv
CaseID,Activity,Timestamp,Duration,Source
20260310_001,Gmail,2026-03-10 10:00:32,120,Browser
20260310_001,Google Sheets,2026-03-10 10:02:32,95,Browser
20260310_001,Slack,2026-03-10 10:04:07,180,Browser
20260310_002,Excel作業,2026-03-10 10:15:00,600,Window
20260310_002,Gmail,2026-03-10 10:25:00,180,Browser
```

**固定カラム（必ず存在する）:**
- `CaseID` — 業務セッションID（`yyyyMMdd_NNN` 形式）
- `Activity` — アクティビティ名（サービス名 or アプリ名）
- `Timestamp` — イベント開始日時

**準固定カラム（現行CSVに存在する）:**
- `Source` — `Browser` or `Window`。`event.source_system` にマッピング

**追加カラム（存在する場合もしない場合もある）:**
- `Duration` — 滞在時間（秒）
- 将来さらにカラムが追加される可能性がある

**設計方針:** 固定3カラム + Source は `event` テーブルの専用カラムにマッピング。それ以外のカラムはCSVヘッダーから動的に検出し、`event_attrs JSONB` に格納する。

**カラムマッピング表:**

| CSVカラム | event テーブルカラム | 備考 |
|-----------|---------------------|------|
| CaseID | case_id | 固定。必須 |
| Activity | activity_name | 固定。必須 |
| Timestamp | event_timestamp | 固定。必須 |
| Source | source_system | 準固定。存在すればマッピング |
| その他すべて | event_attrs (JSONB) | 動的。例: `{"Duration": 120}` |

---

## 技術スタック

| 要素 | 技術 |
|------|------|
| DB | PostgreSQL 16 |
| API | FastAPI + Uvicorn |
| 分析エンジン | pm4py |
| フロントエンド | Streamlit |
| コンテナ | Docker Compose |

---

## ディレクトリ構成

```
nadja-pm-prototype/
├── docker-compose.yml
├── db/
│   └── init.sql          # スキーマ作成
├── api/
│   ├── Dockerfile
│   ├── requirements.txt   # fastapi, uvicorn, pm4py, pandas, sqlalchemy, psycopg2-binary
│   └── app/
│       ├── main.py        # FastAPIアプリ
│       ├── db.py          # DB接続（SQLAlchemy）
│       ├── importer.py    # CSVインポートロジック
│       └── pm_engine.py   # pm4py分析ラッパー
└── streamlit/
    ├── Dockerfile
    ├── requirements.txt   # streamlit, requests, plotly, graphviz（pm4py不要）
    └── app.py             # Streamlitアプリ（全画面1ファイル）
```

---

## DBスキーマ（3テーブル）

```sql
CREATE TABLE process_definition (
    process_id SERIAL PRIMARY KEY,
    process_name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE event (
    event_id BIGSERIAL PRIMARY KEY,
    case_id VARCHAR(255) NOT NULL,
    activity_name VARCHAR(255) NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    process_id INT NOT NULL REFERENCES process_definition(process_id),
    source_system VARCHAR(100),
    event_attrs JSONB
);

CREATE TABLE case_instance (
    case_id VARCHAR(255) PRIMARY KEY,
    process_id INT NOT NULL REFERENCES process_definition(process_id),
    case_start TIMESTAMPTZ,
    case_end TIMESTAMPTZ,
    activity_count INT,
    variant TEXT
);

CREATE INDEX IX_event_case ON event (case_id, event_timestamp);
CREATE INDEX IX_event_activity ON event (activity_name);
CREATE INDEX IX_event_process ON event (process_id, event_timestamp);
CREATE INDEX IX_event_attrs ON event USING GIN (event_attrs);
```

---

## CSVインポート処理（importer.py）

```
1. CSVヘッダー行を読み取り、カラム一覧を取得
2. 必須カラム確認: CaseID, Activity, Timestamp が無ければエラー
3. 各行を処理:
   - CaseID → event.case_id
   - Activity → event.activity_name
   - Timestamp → event.event_timestamp（パース: "yyyy-MM-dd HH:mm:ss"）
   - Source → event.source_system（存在する場合）
   - それ以外のカラム → event.event_attrs に {"Duration": 120} のようにJSON格納
4. process_definition が無ければ作成、あれば既存IDを使用
5. case_instance を自動集計:
   - case_start = MIN(event_timestamp) WHERE case_id
   - case_end = MAX(event_timestamp) WHERE case_id
   - activity_count = COUNT(*) WHERE case_id
   - variant = event_timestamp昇順でソートした上でActivity列をカンマ区切りで連結（例: "Gmail,Google Sheets,Slack"）
     - SQL: `string_agg(activity_name, ',' ORDER BY event_timestamp)`
6. 同一プロセスへの再アップロード → 既存データを削除して上書き（プロトタイプ仕様）
```

---

## APIエンドポイント（4本）

### POST /api/v1/upload/csv

CSVファイルをアップロードし、DBに格納する。

- Request: `multipart/form-data`, field `file` (CSVファイル), field `process_name` (string)
- Response:
```json
{
  "process_id": 1,
  "process_name": "営業事務",
  "imported_events": 150,
  "imported_cases": 12,
  "message": "CSVインポート完了"
}
```
- Error: 必須カラム不足 → 400, パースエラー → 400

### POST /api/v1/discover/dfg

DFG（Directly-Follows Graph）を生成する。

- Request:
```json
{
  "process_id": 1
}
```
- Response:
```json
{
  "nodes": [
    {"name": "Gmail", "count": 45},
    {"name": "Google Sheets", "count": 30},
    {"name": "Slack", "count": 25}
  ],
  "edges": [
    {"from": "Gmail", "to": "Google Sheets", "count": 20, "avg_duration_sec": 95.5},
    {"from": "Google Sheets", "to": "Slack", "count": 15, "avg_duration_sec": 120.0},
    {"from": "Slack", "to": "Gmail", "count": 10, "avg_duration_sec": 60.0}
  ],
  "start_activities": ["Gmail", "Excel作業"],
  "end_activities": ["Slack", "Gmail"]
}
```
- pm4py呼び出し: `pm4py.discover_dfg(df)` → 頻度DFG、`pm4py.discover_performance_dfg(df)` → パフォーマンスDFG
- **avg_duration_sec の計算方法:** エッジの平均所要時間はイベント間のタイムスタンプ差分で算出する（`discover_performance_dfg()` の戻り値、またはDataFrame上で手動計算）。CSVの `Duration` カラム（event_attrs内）はアクティビティ単体の滞在時間であり、エッジの遷移時間とは異なる。

### GET /api/v1/variants?process_id=1

バリアント一覧（業務パターン）を返す。

- Response:
```json
{
  "variants": [
    {"variant": "Gmail → Google Sheets → Slack", "count": 8, "percentage": 66.7, "avg_duration_sec": 420},
    {"variant": "Excel作業 → Gmail", "count": 4, "percentage": 33.3, "avg_duration_sec": 780}
  ],
  "total_cases": 12,
  "total_variants": 2
}
```
- pm4py呼び出し: `pm4py.get_variants(df)`

### GET /api/v1/kpi/summary?process_id=1

基本KPIを返す。

- Response:
```json
{
  "case_count": 12,
  "avg_case_duration_sec": 510.5,
  "avg_activities_per_case": 4.2,
  "variant_count": 2,
  "top_variant_coverage": 0.667,
  "activities": [
    {"name": "Gmail", "count": 45, "avg_duration_sec": 150.0, "total_duration_sec": 6750},
    {"name": "Google Sheets", "count": 30, "avg_duration_sec": 200.0, "total_duration_sec": 6000},
    {"name": "Slack", "count": 25, "avg_duration_sec": 60.0, "total_duration_sec": 1500}
  ]
}
```
- KPI計算: SQLで集計。
  - `avg_case_duration_sec`: 各ケースの `MAX(event_timestamp) - MIN(event_timestamp)` で算出（タイムスタンプ差分）
  - アクティビティ別の `avg_duration_sec` / `total_duration_sec`: `event_attrs->>'Duration'` から取得（存在しない場合はNULL、NULL行はスキップ）

---

## pm4py連携（pm_engine.py）

```python
import pm4py
import pandas as pd

def load_event_log(engine, process_id):
    """PostgreSQLからイベントログをpm4py用DataFrameに変換"""
    query = """
        SELECT case_id, activity_name, event_timestamp,
               (event_attrs->>'Duration')::int AS duration
        FROM event
        WHERE process_id = %(process_id)s
        ORDER BY case_id, event_timestamp
    """
    df = pd.read_sql(query, engine, params={"process_id": process_id})
    df = pm4py.format_dataframe(
        df,
        case_id='case_id',
        activity_key='activity_name',
        timestamp_key='event_timestamp'
    )
    return df
```

---

## Streamlit画面（3画面、1ファイル）

`st.sidebar` でページ切替。FastAPI への通信は `requests` ライブラリ。

### ページ1: CSVアップロード
- `st.file_uploader` でCSV選択
- `st.text_input` でプロセス名入力
- 「アップロード」ボタン → POST /api/v1/upload/csv
- 結果表示: インポート件数、ケース数

### ページ2: プロセスマップ
- `st.selectbox` でプロセス選択
- POST /api/v1/discover/dfg でDFG JSON取得
- **Streamlit側でJSON → Graphviz DOT言語に変換** して `st.graphviz_chart` で表示
  - ノード: アクティビティ名（fontsize/width で頻度を表現）
  - エッジ: 遷移（penwidth で頻度を表現、label に件数表示）
  - 開始/終了アクティビティは色分け（緑=開始、赤=終了）
- **注意:** Streamlitコンテナにpm4pyは不要。APIが返すJSONだけで描画する

### ページ3: KPIダッシュボード
- `st.selectbox` でプロセス選択
- GET /api/v1/kpi/summary でKPI取得
- 上段: `st.metric` カード（ケース数、平均所要時間、バリアント数）
- 中段: アクティビティ別実行回数の棒グラフ（`st.bar_chart` or Plotly）
- 下段: アクティビティ別合計時間の円グラフ（Plotly `px.pie`）
- GET /api/v1/variants でバリアント一覧テーブル

---

## Docker Compose

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: nadja_pm
      POSTGRES_USER: nadja
      POSTGRES_PASSWORD: nadja_dev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U nadja -d nadja_pm"]
      interval: 5s
      timeout: 5s
      retries: 5

  api:
    build: ./api
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://nadja:nadja_dev@postgres:5432/nadja_pm
    depends_on:
      postgres:
        condition: service_healthy

  streamlit:
    build: ./streamlit
    ports:
      - "8501:8501"
    environment:
      API_URL: http://api:8000
    depends_on:
      - api

volumes:
  pgdata:
```

> **注意:** `depends_on` だけではPostgreSQLの起動完了を保証しないため、`healthcheck` + `condition: service_healthy` を使用する。これによりAPIコンテナはPostgreSQLが接続可能になってから起動する。

---

## 起動・検証手順

```bash
# 起動
docker-compose up --build

# ブラウザで開く
# Streamlit: http://localhost:8501
# FastAPI docs: http://localhost:8000/docs

# 検証
# 1. ProcessMiningTestで出力したCSVを用意（なければ下記サンプルを使用）
# 2. Streamlitの「CSVアップロード」ページでアップロード
# 3. 「プロセスマップ」ページでDFGが表示されることを確認
# 4. 「KPIダッシュボード」ページでKPIが表示されることを確認
```

**テスト用サンプルCSV（CSVファイルがない場合）:**

```csv
CaseID,Activity,Timestamp,Duration,Source
20260310_001,Gmail,2026-03-10 09:00:00,120,Browser
20260310_001,Google Sheets,2026-03-10 09:02:00,300,Browser
20260310_001,Slack,2026-03-10 09:07:00,60,Browser
20260310_001,Gmail,2026-03-10 09:08:00,180,Browser
20260310_002,Excel作業,2026-03-10 09:30:00,600,Window
20260310_002,Gmail,2026-03-10 09:40:00,120,Browser
20260310_002,Teams,2026-03-10 09:42:00,300,Window
20260310_002,Excel作業,2026-03-10 09:47:00,900,Window
20260310_003,Gmail,2026-03-10 10:00:00,60,Browser
20260310_003,Google Sheets,2026-03-10 10:01:00,240,Browser
20260310_003,Slack,2026-03-10 10:05:00,120,Browser
20260310_003,Gmail,2026-03-10 10:07:00,60,Browser
20260310_004,Excel作業,2026-03-10 10:30:00,1200,Window
20260310_004,Outlook (メール/予定),2026-03-10 10:50:00,300,Window
20260310_004,Excel作業,2026-03-10 10:55:00,600,Window
20260310_005,Gmail,2026-03-10 11:00:00,90,Browser
20260310_005,Google Sheets,2026-03-10 11:01:30,180,Browser
20260310_005,Slack,2026-03-10 11:04:30,45,Browser
20260310_005,Gmail,2026-03-10 11:05:15,120,Browser
```
