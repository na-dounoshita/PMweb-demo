from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text

from .converter import convert_uiam_log, detect_uiam_format, get_unique_activity_keys
from .db import engine
from .importer import import_csv
from .map_routes import router as map_router
from .pm_engine import discover_dfg, get_variants
from .task_routes import router as task_router

app = FastAPI(title="NADJA PM API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(map_router)
app.include_router(task_router)


class DfgRequest(BaseModel):
    process_id: int


# --- プロセス一覧（Streamlitのselectbox用） ---


@app.get("/api/v1/processes")
def list_processes():
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT process_id, process_name, created_at FROM process_definition ORDER BY created_at DESC")
        ).fetchall()
    return [
        {"process_id": r[0], "process_name": r[1], "created_at": str(r[2])}
        for r in rows
    ]


# --- 1. CSVアップロード ---


@app.post("/api/v1/upload/csv")
async def upload_csv(
    file: UploadFile = File(...),
    process_name: str = Form(...),
    time_gap_minutes: int | None = Form(None),
):
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="CSVファイルを指定してください")

    contents = await file.read()

    try:
        result = import_csv(engine, contents, process_name, time_gap_minutes=time_gap_minutes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


# --- 1b. UIアクティビティモニターログ変換+インポート ---


@app.post("/api/v1/upload/convert-uiam")
async def upload_uiam(
    file: UploadFile = File(...),
    process_name: str = Form(...),
    time_gap_minutes: int | None = Form(None),
    activity_map_json: str | None = Form(None),
    min_duration: int = Form(0),
):
    """UIアクティビティモニターログを変換してインポートする。"""
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="CSVファイルを指定してください")

    import io
    import json

    import pandas as pd

    contents = await file.read()

    try:
        df = pd.read_csv(io.BytesIO(contents), encoding="utf-8-sig")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV読み込みエラー: {e}")

    # activity_map を解析
    activity_map = None
    if activity_map_json:
        try:
            activity_map = json.loads(activity_map_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="activity_map_json が不正なJSONです")

    try:
        converted = convert_uiam_log(df, activity_map=activity_map, min_duration=min_duration)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 変換結果を CSV bytes に変換して既存 import_csv に渡す
    csv_buffer = io.BytesIO()
    converted.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
    csv_bytes = csv_buffer.getvalue()

    try:
        result = import_csv(engine, csv_bytes, process_name, time_gap_minutes=time_gap_minutes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result["message"] = f"UIAMログ変換+インポート完了（{len(converted)}セッション）"
    return result


@app.post("/api/v1/preview/convert-uiam")
async def preview_uiam(
    file: UploadFile = File(...),
    activity_map_json: str | None = Form(None),
    min_duration: int = Form(0),
):
    """UIアクティビティモニターログの変換プレビューを返す。"""
    import io
    import json

    import pandas as pd

    contents = await file.read()

    try:
        df = pd.read_csv(io.BytesIO(contents), encoding="utf-8-sig")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV読み込みエラー: {e}")

    activity_map = None
    if activity_map_json:
        try:
            activity_map = json.loads(activity_map_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="activity_map_json が不正なJSONです")

    try:
        converted = convert_uiam_log(df, activity_map=activity_map, min_duration=min_duration)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ユニークなアクティビティキー一覧（マッピング編集用）
    activity_keys = get_unique_activity_keys(df)

    return {
        "total_sessions": len(converted),
        "preview": converted.head(20).to_dict(orient="records"),
        "activity_keys": activity_keys,
    }


# --- 2. DFG生成 ---


@app.post("/api/v1/discover/dfg")
def api_discover_dfg(req: DfgRequest):
    result = discover_dfg(engine, req.process_id)
    if not result["nodes"]:
        raise HTTPException(status_code=404, detail="指定されたプロセスのデータが見つかりません")
    return result


# --- 3. バリアント一覧 ---


@app.get("/api/v1/variants")
def api_variants(process_id: int):
    result = get_variants(engine, process_id)
    if result["total_cases"] == 0:
        raise HTTPException(status_code=404, detail="指定されたプロセスのデータが見つかりません")
    return result


# --- 4. KPIサマリー ---


@app.get("/api/v1/kpi/summary")
def api_kpi_summary(process_id: int):
    with engine.connect() as conn:
        # 基本KPI（case_instanceから）
        kpi = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS case_count,
                    AVG(EXTRACT(EPOCH FROM (case_end - case_start))) AS avg_case_duration_sec,
                    AVG(activity_count) AS avg_activities_per_case,
                    COUNT(DISTINCT variant) AS variant_count
                FROM case_instance
                WHERE process_id = :pid
            """),
            {"pid": process_id},
        ).fetchone()

        if not kpi or kpi[0] == 0:
            raise HTTPException(status_code=404, detail="指定されたプロセスのデータが見つかりません")

        # トップバリアントカバー率
        top_variant = conn.execute(
            text("""
                SELECT COUNT(*) AS cnt
                FROM case_instance
                WHERE process_id = :pid
                GROUP BY variant
                ORDER BY cnt DESC
                LIMIT 1
            """),
            {"pid": process_id},
        ).fetchone()

        top_coverage = round(top_variant[0] / kpi[0], 3) if top_variant else 0

        # アクティビティ別統計
        activities = conn.execute(
            text("""
                SELECT
                    activity_name,
                    COUNT(*) AS count,
                    AVG((event_attrs->>'Duration')::numeric) AS avg_duration_sec,
                    SUM((event_attrs->>'Duration')::numeric) AS total_duration_sec
                FROM event
                WHERE process_id = :pid
                GROUP BY activity_name
                ORDER BY count DESC
            """),
            {"pid": process_id},
        ).fetchall()

    return {
        "case_count": kpi[0],
        "avg_case_duration_sec": round(float(kpi[1]), 1) if kpi[1] else 0,
        "avg_activities_per_case": round(float(kpi[2]), 1) if kpi[2] else 0,
        "variant_count": kpi[3],
        "top_variant_coverage": top_coverage,
        "activities": [
            {
                "name": a[0],
                "count": a[1],
                "avg_duration_sec": round(float(a[2]), 1) if a[2] else None,
                "total_duration_sec": round(float(a[3]), 1) if a[3] else None,
            }
            for a in activities
        ],
    }
