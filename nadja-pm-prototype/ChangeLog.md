# CHANGELOG

新しい記録を上に追加する。

---

## 2026-03-12 (2)

### やったこと

- Project.md に「画面操作ガイド」セクションを追加
  - サイドバーのナビゲーション、3ページ（CSVアップロード / プロセスマップ / KPIダッシュボード）それぞれのUI要素と操作時の挙動を表形式で記述
  - DFGのノード色分けの凡例、KPIダッシュボードの4セクション構成を明記

### 現在の状態

- Project.md が操作ガイドとして参照可能な状態になった

---

## 2026-03-12

### やったこと

- プロトタイプの全コードを新規作成（設計書 `NADJA_PM_Web_Prototype_Spec.md` に基づく）
  - DB: PostgreSQL スキーマ（3テーブル + 4インデックス）
  - API: FastAPI 5エンドポイント（CSV取込、DFG生成、バリアント一覧、KPIサマリー、プロセス一覧）
  - フロント: Streamlit 3ページ（CSVアップロード、プロセスマップ、KPIダッシュボード）
  - インフラ: Docker Compose（postgres / api / streamlit の3サービス）
- CSVインポートで動的カラムを `event_attrs JSONB` に格納する柔軟な設計を実装
- pm4pyによるDFG生成・バリアント分析をAPIに統合
- Graphviz DOTによるプロセスマップ可視化（開始=緑、終了=ピンク、頻度=線の太さ）
- Plotlyによるアクティビティ別棒グラフ・円グラフを実装

### 現在の状態

- `docker-compose up --build` で起動可能な状態
- http://localhost:8501 でStreamlit UI、http://localhost:8000/docs でSwagger UIにアクセス可能
- 設計書記載のサンプルCSV（19イベント/5ケース）でテスト可能
