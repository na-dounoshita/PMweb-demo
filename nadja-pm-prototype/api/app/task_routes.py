"""タスクマイニング API — タスク定義・タグ付け・タスクレベルDFG"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from .db import engine
from .pm_engine import discover_task_dfg

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


# ---------- Pydantic models ----------


class TaskDefinitionCreate(BaseModel):
    process_id: int
    task_name: str
    description: str | None = None
    color: str | None = None


class TagRequest(BaseModel):
    task_id: int
    case_id: str
    process_id: int
    event_id_start: int
    event_id_end: int


class TaskDfgRequest(BaseModel):
    process_id: int


# ---------- ケース一覧 ----------


@router.get("/cases")
def list_cases(process_id: int):
    """ケース一覧（イベント数・タグ率サマリー）"""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT e.case_id,
                       COUNT(*) AS event_count,
                       MIN(e.event_timestamp) AS case_start,
                       MAX(e.event_timestamp) AS case_end
                FROM event e
                WHERE e.process_id = :pid
                GROUP BY e.case_id
                ORDER BY case_start
            """),
            {"pid": process_id},
        ).fetchall()

        # タグ済みイベント数をケースごとに集計
        tagged = conn.execute(
            text("""
                SELECT ti.case_id, SUM(ti.event_count) AS tagged_count
                FROM task_instance ti
                WHERE ti.process_id = :pid
                GROUP BY ti.case_id
            """),
            {"pid": process_id},
        ).fetchall()
        tagged_map = {r[0]: r[1] for r in tagged}

    return [
        {
            "case_id": r[0],
            "event_count": r[1],
            "case_start": str(r[2]),
            "case_end": str(r[3]),
            "tagged_count": tagged_map.get(r[0], 0),
        }
        for r in rows
    ]


# ---------- イベント一覧 ----------


@router.get("/events")
def list_events(process_id: int, case_id: str):
    """ケース内イベント一覧（タグ状態付き）"""
    with engine.connect() as conn:
        events = conn.execute(
            text("""
                SELECT e.event_id, e.activity_name, e.event_timestamp,
                       e.source_system, e.event_attrs
                FROM event e
                WHERE e.process_id = :pid AND e.case_id = :cid
                ORDER BY e.event_timestamp, e.event_id
            """),
            {"pid": process_id, "cid": case_id},
        ).fetchall()

        # このケースのタスクインスタンス
        tasks = conn.execute(
            text("""
                SELECT ti.task_instance_id, ti.event_id_start, ti.event_id_end,
                       td.task_name, td.color
                FROM task_instance ti
                JOIN task_definition td ON ti.task_id = td.task_id
                WHERE ti.process_id = :pid AND ti.case_id = :cid
                ORDER BY ti.task_start
            """),
            {"pid": process_id, "cid": case_id},
        ).fetchall()

    # イベントID → タスク情報のマッピング
    event_task_map: dict[int, dict] = {}
    for t in tasks:
        for eid in range(t[1], t[2] + 1):
            event_task_map[eid] = {
                "task_instance_id": t[0],
                "task_name": t[3],
                "color": t[4],
            }

    result = []
    for i, e in enumerate(events):
        eid = e[0]
        tag = event_task_map.get(eid)
        result.append({
            "row_num": i + 1,
            "event_id": eid,
            "activity_name": e[1],
            "event_timestamp": str(e[2]),
            "source_system": e[3],
            "task_name": tag["task_name"] if tag else None,
            "task_color": tag["color"] if tag else None,
            "task_instance_id": tag["task_instance_id"] if tag else None,
        })

    return result


# ---------- タスク定義 CRUD ----------


@router.get("/definitions")
def list_definitions(process_id: int):
    """タスク定義一覧"""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT task_id, task_name, description, color, created_at "
                "FROM task_definition WHERE process_id = :pid "
                "ORDER BY created_at"
            ),
            {"pid": process_id},
        ).fetchall()
    return [
        {
            "task_id": r[0],
            "task_name": r[1],
            "description": r[2],
            "color": r[3],
            "created_at": str(r[4]),
        }
        for r in rows
    ]


@router.post("/definitions")
def create_definition(req: TaskDefinitionCreate):
    """タスク定義作成"""
    with engine.begin() as conn:
        existing = conn.execute(
            text(
                "SELECT task_id FROM task_definition "
                "WHERE process_id = :pid AND task_name = :name"
            ),
            {"pid": req.process_id, "name": req.task_name},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="同名のタスク定義が既に存在します")

        row = conn.execute(
            text(
                "INSERT INTO task_definition (process_id, task_name, description, color) "
                "VALUES (:pid, :name, :desc, :color) RETURNING task_id"
            ),
            {
                "pid": req.process_id,
                "name": req.task_name,
                "desc": req.description,
                "color": req.color,
            },
        ).fetchone()
    return {"task_id": row[0], "message": "タスク定義を作成しました"}


@router.delete("/definitions/{task_id}")
def delete_definition(task_id: int):
    """タスク定義削除（cascade で task_instance も削除）"""
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM task_definition WHERE task_id = :tid"),
            {"tid": task_id},
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="タスク定義が見つかりません")
    return {"message": "タスク定義を削除しました"}


# ---------- タグ付け ----------


@router.post("/tag")
def tag_events(req: TagRequest):
    """イベント範囲にタスクタグ付け"""
    with engine.begin() as conn:
        # 1. タスク定義の存在チェック
        task_def = conn.execute(
            text("SELECT task_id FROM task_definition WHERE task_id = :tid"),
            {"tid": req.task_id},
        ).fetchone()
        if not task_def:
            raise HTTPException(status_code=404, detail="タスク定義が見つかりません")

        # 2. 指定範囲のイベントを取得
        events = conn.execute(
            text("""
                SELECT event_id, event_timestamp
                FROM event
                WHERE process_id = :pid AND case_id = :cid
                  AND event_id >= :start AND event_id <= :end
                ORDER BY event_timestamp, event_id
            """),
            {
                "pid": req.process_id,
                "cid": req.case_id,
                "start": req.event_id_start,
                "end": req.event_id_end,
            },
        ).fetchall()

        if not events:
            raise HTTPException(
                status_code=400,
                detail="指定された範囲にイベントが見つかりません",
            )

        # 3. 重複チェック
        overlap = conn.execute(
            text("""
                SELECT task_instance_id
                FROM task_instance
                WHERE process_id = :pid AND case_id = :cid
                  AND NOT (event_id_end < :start OR event_id_start > :end)
                LIMIT 1
            """),
            {
                "pid": req.process_id,
                "cid": req.case_id,
                "start": req.event_id_start,
                "end": req.event_id_end,
            },
        ).fetchone()

        if overlap:
            raise HTTPException(
                status_code=409,
                detail="選択された範囲は既存のタスクと重複しています",
            )

        # 4. 挿入
        task_start = events[0][1]
        task_end = events[-1][1]
        event_count = len(events)

        row = conn.execute(
            text("""
                INSERT INTO task_instance
                    (task_id, case_id, process_id, event_id_start, event_id_end,
                     task_start, task_end, event_count)
                VALUES (:tid, :cid, :pid, :start, :end, :tstart, :tend, :cnt)
                RETURNING task_instance_id
            """),
            {
                "tid": req.task_id,
                "cid": req.case_id,
                "pid": req.process_id,
                "start": req.event_id_start,
                "end": req.event_id_end,
                "tstart": task_start,
                "tend": task_end,
                "cnt": event_count,
            },
        ).fetchone()

    return {"task_instance_id": row[0], "message": "タグ付けしました"}


@router.delete("/tag/{task_instance_id}")
def untag_events(task_instance_id: int):
    """タグ解除"""
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM task_instance WHERE task_instance_id = :tiid"),
            {"tiid": task_instance_id},
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="タグが見つかりません")
    return {"message": "タグを解除しました"}


@router.get("/tag")
def list_tags(process_id: int, case_id: str):
    """ケースのタグ一覧"""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ti.task_instance_id, td.task_name, td.color,
                       ti.event_id_start, ti.event_id_end,
                       ti.task_start, ti.task_end, ti.event_count
                FROM task_instance ti
                JOIN task_definition td ON ti.task_id = td.task_id
                WHERE ti.process_id = :pid AND ti.case_id = :cid
                ORDER BY ti.task_start
            """),
            {"pid": process_id, "cid": case_id},
        ).fetchall()
    return [
        {
            "task_instance_id": r[0],
            "task_name": r[1],
            "color": r[2],
            "event_id_start": r[3],
            "event_id_end": r[4],
            "task_start": str(r[5]),
            "task_end": str(r[6]),
            "event_count": r[7],
        }
        for r in rows
    ]


# ---------- タスクレベルDFG ----------


@router.post("/discover/dfg")
def api_task_dfg(req: TaskDfgRequest):
    """タスクレベルDFGを生成"""
    result = discover_task_dfg(engine, req.process_id)
    if not result["nodes"]:
        raise HTTPException(
            status_code=404,
            detail="タスクデータが見つかりません。先にイベントにタグ付けしてください。",
        )
    return result
