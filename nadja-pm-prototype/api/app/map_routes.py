"""業務マッピング CRUD API"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from .db import engine
from .pm_engine import discover_dfg

router = APIRouter(prefix="/api/v1/maps", tags=["maps"])


# ---------- Pydantic models ----------


class MapSaveRequest(BaseModel):
    process_id: int
    map_name: str = "default"
    source: str = "manual"
    nodes: list[dict]
    edges: list[dict]
    metadata: dict | None = None


class MapFromDfgRequest(BaseModel):
    process_id: int
    map_name: str = "default"


# ---------- CRUD ----------


@router.post("")
def save_map(req: MapSaveRequest):
    """マップを保存（upsert）"""
    with engine.begin() as conn:
        # プロセス存在チェック
        proc = conn.execute(
            text("SELECT process_id FROM process_definition WHERE process_id = :pid"),
            {"pid": req.process_id},
        ).fetchone()
        if not proc:
            raise HTTPException(status_code=404, detail="プロセスが見つかりません")

        existing = conn.execute(
            text(
                "SELECT map_id FROM process_map "
                "WHERE process_id = :pid AND map_name = :name"
            ),
            {"pid": req.process_id, "name": req.map_name},
        ).fetchone()

        nodes_json = json.dumps(req.nodes, ensure_ascii=False)
        edges_json = json.dumps(req.edges, ensure_ascii=False)
        meta_json = json.dumps(req.metadata, ensure_ascii=False) if req.metadata else None

        if existing:
            conn.execute(
                text(
                    "UPDATE process_map "
                    "SET nodes = CAST(:nodes AS jsonb), edges = CAST(:edges AS jsonb), "
                    "    metadata = CAST(:meta AS jsonb), source = :source, "
                    "    updated_at = NOW() "
                    "WHERE map_id = :mid"
                ),
                {
                    "nodes": nodes_json,
                    "edges": edges_json,
                    "meta": meta_json,
                    "source": req.source,
                    "mid": existing[0],
                },
            )
            return {"map_id": existing[0], "message": "マップを更新しました"}
        else:
            row = conn.execute(
                text(
                    "INSERT INTO process_map "
                    "(process_id, map_name, source, nodes, edges, metadata) "
                    "VALUES (:pid, :name, :source, CAST(:nodes AS jsonb), CAST(:edges AS jsonb), CAST(:meta AS jsonb)) "
                    "RETURNING map_id"
                ),
                {
                    "pid": req.process_id,
                    "name": req.map_name,
                    "source": req.source,
                    "nodes": nodes_json,
                    "edges": edges_json,
                    "meta": meta_json,
                },
            ).fetchone()
            return {"map_id": row[0], "message": "マップを作成しました"}


@router.get("")
def list_maps(process_id: int):
    """プロセスのマップ一覧"""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT map_id, map_name, source, created_at, updated_at "
                "FROM process_map WHERE process_id = :pid "
                "ORDER BY updated_at DESC"
            ),
            {"pid": process_id},
        ).fetchall()
    return [
        {
            "map_id": r[0],
            "map_name": r[1],
            "source": r[2],
            "created_at": str(r[3]),
            "updated_at": str(r[4]),
        }
        for r in rows
    ]


@router.get("/{map_id}")
def get_map(map_id: int):
    """マップ取得"""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT map_id, process_id, map_name, source, nodes, edges, "
                "       metadata, created_at, updated_at "
                "FROM process_map WHERE map_id = :mid"
            ),
            {"mid": map_id},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="マップが見つかりません")
    return {
        "map_id": row[0],
        "process_id": row[1],
        "map_name": row[2],
        "source": row[3],
        "nodes": row[4],
        "edges": row[5],
        "metadata": row[6],
        "created_at": str(row[7]),
        "updated_at": str(row[8]),
    }


@router.delete("/{map_id}")
def delete_map(map_id: int):
    """マップ削除"""
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM process_map WHERE map_id = :mid"),
            {"mid": map_id},
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="マップが見つかりません")
    return {"message": "マップを削除しました"}


# ---------- DFG → Map 変換 ----------


@router.post("/from-dfg")
def create_map_from_dfg(req: MapFromDfgRequest):
    """自動生成DFGをマップとして保存"""
    dfg = discover_dfg(engine, req.process_id)
    if not dfg["nodes"]:
        raise HTTPException(status_code=404, detail="DFGデータが見つかりません")

    start_acts = set(dfg.get("start_activities", []))
    end_acts = set(dfg.get("end_activities", []))

    nodes = []
    for i, n in enumerate(dfg["nodes"]):
        name = n["name"]
        node_type = "intermediate"
        if name in start_acts and name in end_acts:
            node_type = "both"
        elif name in start_acts:
            node_type = "start"
        elif name in end_acts:
            node_type = "end"
        nodes.append({
            "id": name,
            "label": name,
            "x": i * 200,
            "y": 0,
            "type": node_type,
            "frequency": n["count"],
        })

    edges = []
    for j, e in enumerate(dfg["edges"]):
        edges.append({
            "id": f"e{j}",
            "from": e["from"],
            "to": e["to"],
            "label": "",
            "frequency": e["count"],
            "avg_duration_sec": e.get("avg_duration_sec"),
        })

    save_req = MapSaveRequest(
        process_id=req.process_id,
        map_name=req.map_name,
        source="auto_dfg",
        nodes=nodes,
        edges=edges,
    )
    return save_map(save_req)


# ---------- エクスポート ----------


@router.get("/{map_id}/export")
def export_map(map_id: int):
    """マップをJSON形式でエクスポート"""
    map_data = get_map(map_id)

    # プロセス名を取得
    with engine.connect() as conn:
        proc = conn.execute(
            text("SELECT process_name FROM process_definition WHERE process_id = :pid"),
            {"pid": map_data["process_id"]},
        ).fetchone()

    export = {
        "format_version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "process_name": proc[0] if proc else "",
        "map_name": map_data["map_name"],
        "nodes": map_data["nodes"],
        "edges": map_data["edges"],
        "metadata": map_data["metadata"],
    }
    return JSONResponse(
        content=export,
        headers={"Content-Disposition": f'attachment; filename="map_{map_id}.json"'},
    )


# ---------- インポート ----------


@router.post("/import")
async def import_map(
    file: UploadFile = File(...),
    process_id: int = Form(...),
    map_name: str = Form("imported"),
):
    """マップをインポート（JSON or CSV自動判定）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="ファイルを指定してください")

    contents = await file.read()
    text_content = contents.decode("utf-8-sig")

    if file.filename.endswith(".json"):
        return _import_json(text_content, process_id, map_name)
    elif file.filename.endswith(".csv"):
        return _import_csv(text_content, process_id, map_name)
    else:
        raise HTTPException(
            status_code=400,
            detail="対応形式: .json または .csv",
        )


def _import_json(content: str, process_id: int, map_name: str) -> dict:
    """JSONファイルからマップをインポート"""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSONの解析に失敗しました")

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if not nodes and not edges:
        raise HTTPException(status_code=400, detail="ノードまたはエッジが必要です")

    save_req = MapSaveRequest(
        process_id=process_id,
        map_name=map_name,
        source="imported",
        nodes=nodes,
        edges=edges,
        metadata=data.get("metadata"),
    )
    return save_map(save_req)


def _import_csv(content: str, process_id: int, map_name: str) -> dict:
    """CSVファイル（From,To,Label形式）からマップをインポート"""
    reader = csv.DictReader(io.StringIO(content))
    fields = reader.fieldnames or []

    # イベントログCSVとの判定
    if "Activity" in fields and "Timestamp" in fields:
        raise HTTPException(
            status_code=400,
            detail="イベントログCSVは「CSVアップロード」ページからインポートしてください",
        )

    if "From" not in fields or "To" not in fields:
        raise HTTPException(
            status_code=400,
            detail="CSV形式エラー: 'From' と 'To' カラムが必要です",
        )

    node_names: set[str] = set()
    edges: list[dict] = []
    for i, row in enumerate(reader):
        src = row["From"].strip()
        dst = row["To"].strip()
        if not src or not dst:
            continue
        node_names.add(src)
        node_names.add(dst)
        edges.append({
            "id": f"e{i}",
            "from": src,
            "to": dst,
            "label": row.get("Label", "").strip(),
        })

    if not edges:
        raise HTTPException(status_code=400, detail="有効なエッジが見つかりません")

    nodes = [
        {"id": name, "label": name, "x": idx * 200, "y": 0, "type": "intermediate"}
        for idx, name in enumerate(sorted(node_names))
    ]

    save_req = MapSaveRequest(
        process_id=process_id,
        map_name=map_name,
        source="imported",
        nodes=nodes,
        edges=edges,
    )
    return save_map(save_req)
