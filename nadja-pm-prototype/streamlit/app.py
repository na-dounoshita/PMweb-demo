import io
import json
import os
from collections import deque

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="NADJA PM", page_icon="📊", layout="wide")
st.sidebar.title("NADJA Process Mining")

page = st.sidebar.radio("ページ選択", ["CSVアップロード", "プロセスマップ", "KPIダッシュボード"])


def format_duration(seconds: float | None) -> str:
    """秒数を読みやすい形式に変換する"""
    if seconds is None:
        return ""
    seconds = abs(seconds)
    if seconds < 60:
        return f"{seconds:.0f}秒"
    elif seconds < 3600:
        m, s = int(seconds // 60), int(seconds % 60)
        return f"{m}分{s}秒"
    elif seconds < 86400:
        h, m = int(seconds // 3600), int((seconds % 3600) // 60)
        return f"{h}時間{m}分"
    else:
        d, h = int(seconds // 86400), int((seconds % 86400) // 3600)
        return f"{d}日{h}時間"


def duration_color(value: float, min_val: float, max_val: float) -> str:
    """所要時間を青(速い)→黄→赤(遅い)のグラデーションに変換"""
    if max_val <= min_val:
        return "#2196F3"
    t = (value - min_val) / (max_val - min_val)
    if t < 0.5:
        r = int(33 + (255 - 33) * (t * 2))
        g = int(150 + (193 - 150) * (t * 2))
        b = int(243 + (7 - 243) * (t * 2))
    else:
        t2 = (t - 0.5) * 2
        r = int(255 - (255 - 244) * t2)
        g = int(193 - (193 - 67) * t2)
        b = int(7 - (7 - 54) * t2)
    return f"#{r:02x}{g:02x}{b:02x}"


def get_processes():
    """プロセス一覧を取得"""
    try:
        resp = requests.get(f"{API_URL}/api/v1/processes", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


# ===== ページ1: CSVアップロード =====

if page == "CSVアップロード":
    st.header("CSVアップロード")

    st.info("アップロードしたデータはデータベースに保存されます。同じプロセス名で再アップロードすると既存データは上書きされます。")

    uploaded_file = st.file_uploader("CSVファイルを選択", type=["csv"])
    process_name = st.text_input("プロセス名", placeholder="例: 営業事務")

    time_gap_minutes = None
    if uploaded_file is not None:
        header_df = pd.read_csv(io.BytesIO(uploaded_file.getvalue()), encoding="utf-8-sig", nrows=0)
        has_case_id = "CaseID" in header_df.columns

        if not has_case_id:
            st.warning("CSVにCaseIDカラムがありません。タイムギャップ閾値を指定してケースIDを自動生成します。")
            time_gap_minutes = st.number_input(
                "タイムギャップ閾値（分）",
                min_value=1,
                max_value=1440,
                value=30,
                step=5,
                help="この時間以上の間隔があると新しいケースとして分割されます",
            )
        else:
            st.info("CaseIDカラムが検出されました。既存のケースIDを使用します。")

    if st.button("アップロード", disabled=not (uploaded_file and process_name)):
        with st.spinner("インポート中..."):
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")}
            data = {"process_name": process_name}
            if time_gap_minutes is not None:
                data["time_gap_minutes"] = str(time_gap_minutes)
            try:
                resp = requests.post(
                    f"{API_URL}/api/v1/upload/csv", files=files, data=data, timeout=60
                )
                resp.raise_for_status()
                result = resp.json()
                st.success(result["message"])
                col1, col2 = st.columns(2)
                col1.metric("インポートイベント数", result["imported_events"])
                col2.metric("ケース数", result["imported_cases"])
            except requests.exceptions.HTTPError as e:
                detail = e.response.json().get("detail", str(e)) if e.response else str(e)
                st.error(f"エラー: {detail}")
            except Exception as e:
                st.error(f"通信エラー: {e}")


# ===== ページ2: プロセスマップ =====

elif page == "プロセスマップ":
    st.header("プロセスマップ（DFG）")

    processes = get_processes()
    if not processes:
        st.info("プロセスが登録されていません。先にCSVをアップロードしてください。")
    else:
        options = {p["process_name"]: p["process_id"] for p in processes}
        selected = st.selectbox("プロセス選択", list(options.keys()))
        process_id = options[selected]

        try:
            resp = requests.post(
                f"{API_URL}/api/v1/discover/dfg",
                json={"process_id": process_id},
                timeout=30,
            )
            resp.raise_for_status()
            dfg = resp.json()

            if not dfg["nodes"]:
                st.info("表示するデータがありません。")
            else:
                start_acts = set(dfg.get("start_activities", []))
                end_acts = set(dfg.get("end_activities", []))
                edges = dfg["edges"]
                max_edge_count = max((e["count"] for e in edges), default=1)

                # --- サイドバー: フィルタ設定 ---
                st.sidebar.divider()
                st.sidebar.subheader("フィルタ設定")
                min_freq = st.sidebar.slider(
                    "最小頻度フィルタ", 1, max(max_edge_count, 1), 1,
                    help="この値未満の頻度のエッジを非表示にします",
                )
                show_duration = st.sidebar.checkbox("所要時間を表示", value=True)
                highlight_critical = st.sidebar.checkbox("クリティカルパス強調", value=False)

                # フィルタ適用
                filtered_edges = [e for e in edges if e["count"] >= min_freq]

                # クリティカルパスのエッジセット
                critical_set = set()
                if highlight_critical:
                    for ce in dfg.get("critical_path_edges", []):
                        critical_set.add((ce["from"], ce["to"]))

                # 所要時間の最小・最大（エッジ色計算用）
                durations = [
                    e["avg_duration_sec"] for e in filtered_edges
                    if e.get("avg_duration_sec") is not None
                ]
                min_dur = min(durations) if durations else 0
                max_dur = max(durations) if durations else 0

                # フィルタ後に使われるノードだけ表示
                active_nodes = set()
                for e in filtered_edges:
                    active_nodes.add(e["from"])
                    active_nodes.add(e["to"])
                # ノードが1つもない場合は全ノード表示
                if not active_nodes:
                    active_nodes = {n["name"] for n in dfg["nodes"]}

                # --- BFSでノードレベル計算（LRレイアウト用） ---
                adjacency: dict[str, list[str]] = {}
                for e in filtered_edges:
                    adjacency.setdefault(e["from"], []).append(e["to"])

                node_levels: dict[str, int] = {}
                queue: deque[str] = deque()
                for s in start_acts:
                    if s in active_nodes:
                        node_levels[s] = 0
                        queue.append(s)

                while queue:
                    current = queue.popleft()
                    for neighbor in adjacency.get(current, []):
                        if neighbor not in node_levels:
                            node_levels[neighbor] = node_levels[current] + 1
                            queue.append(neighbor)

                for n in active_nodes:
                    if n not in node_levels:
                        node_levels[n] = 0

                # レベルからx/y座標を計算
                level_groups: dict[int, list[str]] = {}
                for name, level in node_levels.items():
                    level_groups.setdefault(level, []).append(name)

                LEVEL_SEP = 350
                NODE_SEP = 150
                node_positions: dict[str, tuple[int, int]] = {}
                for level, names in level_groups.items():
                    x = level * LEVEL_SEP
                    total_height = (len(names) - 1) * NODE_SEP
                    start_y = -total_height // 2
                    for i, name in enumerate(names):
                        node_positions[name] = (x, start_y + i * NODE_SEP)

                # --- vis.js データ構築 ---
                node_colors = {
                    "start_end": {"background": "#FF9800", "border": "#E65100"},
                    "start": {"background": "#4CAF50", "border": "#2E7D32"},
                    "end": {"background": "#F44336", "border": "#C62828"},
                    "middle": {"background": "#78909C", "border": "#546E7A"},
                }
                max_node_count = max((n["count"] for n in dfg["nodes"]), default=1)

                vis_nodes = []
                for node in dfg["nodes"]:
                    name = node["name"]
                    if name not in active_nodes:
                        continue
                    count = node["count"]

                    if name in start_acts and name in end_acts:
                        color = node_colors["start_end"]
                        node_type = "開始+終了"
                    elif name in start_acts:
                        color = node_colors["start"]
                        node_type = "開始"
                    elif name in end_acts:
                        color = node_colors["end"]
                        node_type = "終了"
                    else:
                        color = node_colors["middle"]
                        node_type = "中間"

                    pos = node_positions.get(name, (0, 0))
                    vis_nodes.append({
                        "id": name,
                        "label": f"{name}\n({count})",
                        "x": pos[0],
                        "y": pos[1],
                        "fixed": True,
                        "color": color,
                        "shape": "box",
                        "borderWidth": 2,
                        "font": {"size": 14, "color": "#FFFFFF"},
                        "title": f"アクティビティ: {name}\n実行回数: {count}\n種類: {node_type}",
                    })

                # 双方向エッジ検出
                edge_set = {(e["from"], e["to"]) for e in filtered_edges}

                vis_edges = []
                for edge in filtered_edges:
                    count = edge["count"]
                    avg_dur = edge.get("avg_duration_sec")
                    width = max(1.0, 8.0 * count / max_edge_count)

                    if avg_dur is not None:
                        edge_color = duration_color(avg_dur, min_dur, max_dur)
                    else:
                        edge_color = "#999999"

                    is_critical = (edge["from"], edge["to"]) in critical_set
                    if highlight_critical and not is_critical:
                        edge_color = "#DDDDDD"
                        width = max(1.0, width * 0.5)
                    elif is_critical:
                        width = min(width + 3, 12)

                    if show_duration and avg_dur is not None:
                        label = f"{count} ({format_duration(avg_dur)})"
                    else:
                        label = str(count)

                    tooltip = (
                        f"{edge['from']} → {edge['to']}\n"
                        f"頻度: {count}回\n"
                        f"平均所要時間: {format_duration(avg_dur) if avg_dur else 'N/A'}"
                    )

                    # 双方向エッジはカーブで分離（両方CWでfrom/to反転により逆方向にカーブ）
                    reverse = (edge["to"], edge["from"])
                    if reverse in edge_set:
                        smooth = {"type": "curvedCW", "roundness": 0.4}
                    else:
                        smooth = {"type": "curvedCW", "roundness": 0.15}

                    vis_edges.append({
                        "from": edge["from"],
                        "to": edge["to"],
                        "label": label,
                        "width": width,
                        "color": {"color": edge_color},
                        "arrows": "to",
                        "font": {"size": 11, "align": "horizontal", "background": "white"},
                        "smooth": smooth,
                        "title": tooltip,
                    })

                # vis.js HTML直接生成（pyvis不使用）
                nodes_json = json.dumps(vis_nodes, ensure_ascii=False)
                edges_json = json.dumps(vis_edges, ensure_ascii=False)
                vis_html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  html, body {{ margin:0; padding:0; width:100%; height:100%; overflow:hidden; }}
  #graph {{ width:100%; height:620px; }}
</style>
</head><body>
<div id="graph"></div>
<script>
document.addEventListener("DOMContentLoaded", function() {{
  var nodes = new vis.DataSet({nodes_json});
  var edges = new vis.DataSet({edges_json});
  var container = document.getElementById("graph");
  var options = {{
    layout: {{ hierarchical: false }},
    physics: {{ enabled: false }},
    edges: {{
      font: {{ align: "horizontal" }}
    }},
    interaction: {{
      hover: true,
      zoomView: true,
      dragView: true,
      dragNodes: true,
      tooltipDelay: 100
    }}
  }};
  var network = new vis.Network(container, {{nodes: nodes, edges: edges}}, options);
  network.once("afterDrawing", function() {{
    network.fit({{ animation: false }});
  }});
}});
</script>
</body></html>"""
                components.html(vis_html, height=650, scrolling=False)

                # 凡例
                st.markdown(
                    """
                    <div style="display:flex; gap:16px; flex-wrap:wrap; padding:8px 0; font-size:14px;">
                        <span><span style="background:#4CAF50; color:white; padding:2px 8px; border-radius:4px;">■</span> 開始</span>
                        <span><span style="background:#F44336; color:white; padding:2px 8px; border-radius:4px;">■</span> 終了</span>
                        <span><span style="background:#FF9800; color:white; padding:2px 8px; border-radius:4px;">■</span> 開始+終了</span>
                        <span><span style="background:#78909C; color:white; padding:2px 8px; border-radius:4px;">■</span> 中間</span>
                        <span>｜ エッジ色: <span style="color:#2196F3;">■</span>速い → <span style="color:#FFC107;">■</span>中間 → <span style="color:#F44336;">■</span>遅い</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        except requests.exceptions.HTTPError as e:
            detail = e.response.json().get("detail", str(e)) if e.response else str(e)
            st.warning(detail)
        except Exception as e:
            st.error(f"通信エラー: {e}")


# ===== ページ3: KPIダッシュボード =====

elif page == "KPIダッシュボード":
    st.header("KPIダッシュボード")

    processes = get_processes()
    if not processes:
        st.info("プロセスが登録されていません。先にCSVをアップロードしてください。")
    else:
        options = {p["process_name"]: p["process_id"] for p in processes}
        selected = st.selectbox("プロセス選択", list(options.keys()))
        process_id = options[selected]

        try:
            # KPI取得
            kpi_resp = requests.get(
                f"{API_URL}/api/v1/kpi/summary", params={"process_id": process_id}, timeout=30
            )
            kpi_resp.raise_for_status()
            kpi = kpi_resp.json()

            # 上段: メトリクスカード
            col1, col2, col3 = st.columns(3)

            avg_dur = kpi["avg_case_duration_sec"]
            dur_display = format_duration(avg_dur)

            col1.metric("ケース数", kpi["case_count"])
            col2.metric("平均所要時間", dur_display)
            col3.metric("バリアント数", kpi["variant_count"])

            st.divider()

            # 中段: アクティビティ別実行回数（棒グラフ）
            activities = kpi.get("activities", [])
            if activities:
                st.subheader("アクティビティ別実行回数")
                fig_bar = px.bar(
                    activities,
                    x="name",
                    y="count",
                    labels={"name": "アクティビティ", "count": "実行回数"},
                )
                fig_bar.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig_bar, use_container_width=True)

            # 下段左: 合計時間の円グラフ
            activities_with_duration = [a for a in activities if a.get("total_duration_sec")]
            if activities_with_duration:
                st.subheader("アクティビティ別合計時間")
                fig_pie = px.pie(
                    activities_with_duration,
                    names="name",
                    values="total_duration_sec",
                )
                st.plotly_chart(fig_pie, use_container_width=True)

            st.divider()

            # バリアント一覧
            var_resp = requests.get(
                f"{API_URL}/api/v1/variants", params={"process_id": process_id}, timeout=30
            )
            var_resp.raise_for_status()
            var_data = var_resp.json()

            st.subheader("バリアント一覧")
            if var_data["variants"]:
                variants_display = var_data["variants"].copy()
                for v in variants_display:
                    v["avg_duration"] = format_duration(v.get("avg_duration_sec"))
                st.dataframe(
                    variants_display,
                    column_config={
                        "variant": "バリアント",
                        "count": "件数",
                        "percentage": st.column_config.NumberColumn("割合(%)", format="%.1f"),
                        "avg_duration": "平均所要時間",
                        "avg_duration_sec": None,
                    },
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("バリアントデータがありません")

        except requests.exceptions.HTTPError as e:
            detail = e.response.json().get("detail", str(e)) if e.response else str(e)
            st.warning(detail)
        except Exception as e:
            st.error(f"通信エラー: {e}")
