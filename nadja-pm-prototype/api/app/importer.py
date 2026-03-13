import io
import json

import pandas as pd
from sqlalchemy import text

REQUIRED_COLUMNS = {"Activity", "Timestamp"}
KNOWN_COLUMNS = {"Source"}  # 専用カラムにマッピングされる準固定カラム


def _generate_case_ids(df: pd.DataFrame, time_gap_minutes: int) -> pd.DataFrame:
    """タイムスタンプ間のギャップが閾値を超えたら新しいケースとして分割する。"""
    df = df.sort_values("Timestamp").reset_index(drop=True)
    threshold = pd.Timedelta(minutes=time_gap_minutes)

    case_ids = []
    case_seq = 1
    current_date = None

    for i, row in df.iterrows():
        date_str = row["Timestamp"].strftime("%Y%m%d")

        if i == 0:
            current_date = date_str
        elif date_str != current_date:
            current_date = date_str
            case_seq = 1
        elif row["Timestamp"] - df.loc[i - 1, "Timestamp"] > threshold:
            case_seq += 1

        case_ids.append(f"{current_date}_{case_seq:03d}")

    df["CaseID"] = case_ids
    return df


def import_csv(engine, file_bytes: bytes, process_name: str, time_gap_minutes: int | None = None) -> dict:
    """CSVをパースしてDBに格納する。同一プロセスへの再アップロードは上書き。"""

    df = pd.read_csv(io.BytesIO(file_bytes), encoding="utf-8-sig")

    # 必須カラム確認
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"必須カラムが不足しています: {missing}")

    has_case_id = "CaseID" in df.columns
    if not has_case_id and time_gap_minutes is None:
        raise ValueError(
            "CaseIDカラムがありません。タイムギャップ閾値を指定してケースIDを自動生成してください。"
        )

    # Timestampパース
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])

    # CaseIDが無い場合、タイムギャップで自動生成
    if not has_case_id:
        df = _generate_case_ids(df, time_gap_minutes)

    # 動的カラム（固定・準固定以外）→ event_attrs JSONB
    fixed_columns = {"CaseID", "Activity", "Timestamp"}
    extra_columns = [
        c for c in df.columns if c not in fixed_columns and c not in KNOWN_COLUMNS
    ]

    def build_attrs(row):
        attrs = {}
        for col in extra_columns:
            val = row[col]
            if pd.notna(val):
                attrs[col] = val
                # int/floatをPython標準型に変換（JSON直列化用）
                if hasattr(val, "item"):
                    attrs[col] = val.item()
        return json.dumps(attrs) if attrs else None

    df["event_attrs"] = df.apply(build_attrs, axis=1)

    with engine.begin() as conn:
        # プロセス定義の取得または作成
        row = conn.execute(
            text("SELECT process_id FROM process_definition WHERE process_name = :name"),
            {"name": process_name},
        ).fetchone()

        if row:
            process_id = row[0]
            # 既存データ削除（再アップロード = 上書き）
            conn.execute(
                text("DELETE FROM case_instance WHERE process_id = :pid"),
                {"pid": process_id},
            )
            conn.execute(
                text("DELETE FROM event WHERE process_id = :pid"),
                {"pid": process_id},
            )
        else:
            result = conn.execute(
                text(
                    "INSERT INTO process_definition (process_name) VALUES (:name) RETURNING process_id"
                ),
                {"name": process_name},
            )
            process_id = result.fetchone()[0]

        # イベント挿入
        insert_sql = text("""
            INSERT INTO event (case_id, activity_name, event_timestamp, process_id, source_system, event_attrs)
            VALUES (:case_id, :activity_name, :event_timestamp, :process_id, :source_system, CAST(:event_attrs AS jsonb))
        """)

        records = []
        for _, r in df.iterrows():
            records.append({
                "case_id": r["CaseID"],
                "activity_name": r["Activity"],
                "event_timestamp": r["Timestamp"],
                "process_id": process_id,
                "source_system": r.get("Source") if pd.notna(r.get("Source")) else None,
                "event_attrs": r["event_attrs"],
            })

        conn.execute(insert_sql, records)

        # case_instance 自動集計
        conn.execute(
            text("""
                INSERT INTO case_instance (case_id, process_id, case_start, case_end, activity_count, variant)
                SELECT
                    case_id,
                    :pid,
                    MIN(event_timestamp),
                    MAX(event_timestamp),
                    COUNT(*),
                    string_agg(activity_name, ',' ORDER BY event_timestamp)
                FROM event
                WHERE process_id = :pid
                GROUP BY case_id
            """),
            {"pid": process_id},
        )

        # 結果集計
        stats = conn.execute(
            text("""
                SELECT COUNT(*) AS event_count, COUNT(DISTINCT case_id) AS case_count
                FROM event WHERE process_id = :pid
            """),
            {"pid": process_id},
        ).fetchone()

    return {
        "process_id": process_id,
        "process_name": process_name,
        "imported_events": stats[0],
        "imported_cases": stats[1],
        "message": "CSVインポート完了",
    }
