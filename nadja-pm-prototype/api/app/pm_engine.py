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
            # perf_dfgの値は平均秒数（float）
            avg_dur = round(float(perf_val), 1)
        edges.append({
            "from": src,
            "to": dst,
            "count": count,
            "avg_duration_sec": avg_dur,
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "start_activities": list(start_acts.keys()),
        "end_activities": list(end_acts.keys()),
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
