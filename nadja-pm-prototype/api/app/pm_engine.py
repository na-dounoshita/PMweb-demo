import pm4py
import pandas as pd
from sqlalchemy import text


def load_event_log(engine, process_id: int) -> pd.DataFrame:
    """PostgreSQLからイベントログをpm4py用DataFrameに変換"""
    query = """
        SELECT case_id, activity_name, event_timestamp,
               (event_attrs->>'Duration')::int AS duration
        FROM event
        WHERE process_id = %(process_id)s
        ORDER BY case_id, event_timestamp
    """
    df = pd.read_sql(query, engine, params={"process_id": process_id})

    if df.empty:
        return df

    df = pm4py.format_dataframe(
        df,
        case_id="case_id",
        activity_key="activity_name",
        timestamp_key="event_timestamp",
    )
    return df


def _compute_critical_path(freq_dfg: dict, start_acts: dict, end_acts: dict) -> list:
    """最高頻度のエッジを辿ってクリティカルパスを特定する"""
    adj: dict[str, tuple[str, int]] = {}
    for (src, dst), count in freq_dfg.items():
        if src not in adj or count > adj[src][1]:
            adj[src] = (dst, count)

    if not start_acts:
        return []
    best_start = max(start_acts.items(), key=lambda x: x[1])[0]
    path_edges = []
    visited: set[str] = set()
    current = best_start
    while current in adj and current not in visited:
        visited.add(current)
        next_node, _ = adj[current]
        path_edges.append({"from": current, "to": next_node})
        if next_node in end_acts:
            break
        current = next_node
    return path_edges


def discover_dfg(engine, process_id: int) -> dict:
    """DFG（Directly-Follows Graph）を生成する"""
    df = load_event_log(engine, process_id)
    if df.empty:
        return {"nodes": [], "edges": [], "start_activities": [], "end_activities": []}

    # 頻度DFG
    freq_dfg, start_acts, end_acts = pm4py.discover_dfg(df)

    # パフォーマンスDFG
    perf_dfg, _, _ = pm4py.discover_performance_dfg(df)

    # ノード集計（アクティビティ別出現回数）
    activity_counts = df["activity_name"].value_counts().to_dict()
    nodes = [{"name": name, "count": count} for name, count in activity_counts.items()]

    # エッジ変換
    edges = []
    for (src, dst), count in freq_dfg.items():
        avg_dur = None
        perf_val = perf_dfg.get((src, dst))
        if perf_val is not None:
            # pm4pyバージョンにより float or dict（{"mean": ..., ...}）が返る
            if isinstance(perf_val, dict):
                perf_val = perf_val.get("mean", 0)
            avg_dur = round(float(perf_val), 1)
        edges.append({
            "from": src,
            "to": dst,
            "count": count,
            "avg_duration_sec": avg_dur,
        })

    critical_path = _compute_critical_path(freq_dfg, start_acts, end_acts)

    return {
        "nodes": nodes,
        "edges": edges,
        "start_activities": list(start_acts.keys()),
        "end_activities": list(end_acts.keys()),
        "critical_path_edges": critical_path,
    }


def load_task_event_log(engine, process_id: int) -> pd.DataFrame:
    """タスクレベルのイベントログを構築する。

    タグ付きイベント → task_name をアクティビティとする
    未タグの連続イベント → 「その他」としてまとめる
    """
    # 全イベント取得
    events_query = """
        SELECT e.event_id, e.case_id, e.activity_name, e.event_timestamp
        FROM event e
        WHERE e.process_id = %(process_id)s
        ORDER BY e.case_id, e.event_timestamp, e.event_id
    """
    events_df = pd.read_sql(events_query, engine, params={"process_id": process_id})
    if events_df.empty:
        return pd.DataFrame()

    # タスクインスタンス取得
    tasks_query = """
        SELECT ti.case_id, ti.event_id_start, ti.event_id_end,
               td.task_name, ti.task_start, ti.task_end
        FROM task_instance ti
        JOIN task_definition td ON ti.task_id = td.task_id
        WHERE ti.process_id = %(process_id)s
        ORDER BY ti.case_id, ti.task_start
    """
    tasks_df = pd.read_sql(tasks_query, engine, params={"process_id": process_id})

    # event_id → task_name マッピング
    event_task: dict[int, str] = {}
    if not tasks_df.empty:
        for _, t in tasks_df.iterrows():
            for eid in range(int(t["event_id_start"]), int(t["event_id_end"]) + 1):
                event_task[eid] = t["task_name"]

    # ケースごとにタスクレベルのログを構築
    rows: list[dict] = []
    for case_id, group in events_df.groupby("case_id", sort=False):
        prev_label: str | None = None
        for _, evt in group.iterrows():
            label = event_task.get(int(evt["event_id"]), "その他")
            if label != prev_label:
                rows.append({
                    "case_id": case_id,
                    "activity_name": label,
                    "event_timestamp": evt["event_timestamp"],
                })
                prev_label = label

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = pm4py.format_dataframe(
        df,
        case_id="case_id",
        activity_key="activity_name",
        timestamp_key="event_timestamp",
    )
    return df


def discover_task_dfg(engine, process_id: int) -> dict:
    """タスクレベルのDFGを生成する"""
    df = load_task_event_log(engine, process_id)
    if df.empty:
        return {"nodes": [], "edges": [], "start_activities": [], "end_activities": [], "critical_path_edges": []}

    freq_dfg, start_acts, end_acts = pm4py.discover_dfg(df)
    perf_dfg, _, _ = pm4py.discover_performance_dfg(df)

    activity_counts = df["activity_name"].value_counts().to_dict()
    nodes = [{"name": name, "count": count} for name, count in activity_counts.items()]

    edges = []
    for (src, dst), count in freq_dfg.items():
        avg_dur = None
        perf_val = perf_dfg.get((src, dst))
        if perf_val is not None:
            if isinstance(perf_val, dict):
                perf_val = perf_val.get("mean", 0)
            avg_dur = round(float(perf_val), 1)
        edges.append({
            "from": src,
            "to": dst,
            "count": count,
            "avg_duration_sec": avg_dur,
        })

    critical_path = _compute_critical_path(freq_dfg, start_acts, end_acts)

    return {
        "nodes": nodes,
        "edges": edges,
        "start_activities": list(start_acts.keys()),
        "end_activities": list(end_acts.keys()),
        "critical_path_edges": critical_path,
    }


def get_variants(engine, process_id: int) -> dict:
    """バリアント一覧を取得する"""
    df = load_event_log(engine, process_id)
    if df.empty:
        return {"variants": [], "total_cases": 0, "total_variants": 0}

    variants = pm4py.get_variants(df)

    # ケースごとの所要時間を計算
    case_durations = (
        df.groupby("case_id")["event_timestamp"]
        .agg(lambda x: (x.max() - x.min()).total_seconds())
        .to_dict()
    )

    # ケースごとのバリアントを特定
    case_variants = (
        df.sort_values("event_timestamp")
        .groupby("case_id")["activity_name"]
        .agg(lambda x: ",".join(x))
        .to_dict()
    )

    # バリアントごとの平均所要時間
    variant_durations: dict[str, list[float]] = {}
    for case_id, variant_str in case_variants.items():
        variant_durations.setdefault(variant_str, []).append(
            case_durations.get(case_id, 0)
        )

    total_cases = len(case_variants)
    result_variants = []
    for variant_key, case_list in variants.items():
        # variant_keyはタプルの場合がある
        if isinstance(variant_key, tuple):
            variant_str = ",".join(variant_key)
        else:
            variant_str = str(variant_key)

        count = len(case_list) if isinstance(case_list, list) else case_list
        display_str = variant_str.replace(",", " → ")

        durations = variant_durations.get(variant_str, [])
        avg_dur = round(sum(durations) / len(durations), 1) if durations else 0

        result_variants.append({
            "variant": display_str,
            "count": count,
            "percentage": round(count / total_cases * 100, 1) if total_cases else 0,
            "avg_duration_sec": avg_dur,
        })

    result_variants.sort(key=lambda v: v["count"], reverse=True)

    return {
        "variants": result_variants,
        "total_cases": total_cases,
        "total_variants": len(result_variants),
    }
