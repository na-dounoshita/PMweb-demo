import csv
import io
import json
import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components

API_URL = os.environ.get("API_URL", "http://localhost:8000")
PUBLIC_API_URL = os.environ.get("PUBLIC_API_URL", "http://localhost:8000")

st.set_page_config(page_title="NADJA PM", page_icon="📊", layout="wide")
st.sidebar.title("NADJA Process Mining")

page = st.sidebar.radio("ページ選択", ["CSVアップロード", "プロセスマップ", "タスクマイニング", "プロセスマップα", "KPIダッシュボード"])


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

        # 表示レベル切替
        st.sidebar.divider()
        dfg_level = st.sidebar.radio(
            "表示レベル", ["ウィンドウレベル", "タスクレベル"],
            help="タスクレベル: タスクマイニングでタグ付けした業務タスク単位でDFGを表示",
        )

        try:
            if dfg_level == "タスクレベル":
                resp = requests.post(
                    f"{API_URL}/api/v1/tasks/discover/dfg",
                    json={"process_id": process_id},
                    timeout=30,
                )
            else:
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
                layout_direction = st.sidebar.radio(
                    "レイアウト方向", ["上→下", "左→右"], horizontal=True
                )

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

                # --- Cytoscape.js + Dagre でプロセスマップ描画 ---
                rank_dir = "TB" if layout_direction == "上→下" else "LR"

                # ノード・エッジデータをJSON化
                cy_nodes = []
                for node in dfg["nodes"]:
                    name = node["name"]
                    if name not in active_nodes:
                        continue
                    node_type = "startEnd" if (name in start_acts and name in end_acts) \
                        else "start" if name in start_acts \
                        else "end" if name in end_acts \
                        else "mid"
                    cy_nodes.append({
                        "id": name,
                        "label": f"{name}\n({node['count']})",
                        "count": node["count"],
                        "nodeType": node_type,
                    })

                max_edge_count_filtered = max((e["count"] for e in filtered_edges), default=1)

                cy_edges = []
                for edge in filtered_edges:
                    is_critical = (edge["from"], edge["to"]) in critical_set
                    cy_edges.append({
                        "source": edge["from"],
                        "target": edge["to"],
                        "count": edge["count"],
                        "avgDur": edge.get("avg_duration_sec"),
                        "width": max(1, (edge["count"] / max_edge_count_filtered) * 6),
                        "isCritical": is_critical,
                    })

                nodes_json = json.dumps(cy_nodes, ensure_ascii=False)
                edges_json = json.dumps(cy_edges, ensure_ascii=False)
                show_dur_js = "true" if show_duration else "false"
                highlight_crit_js = "true" if highlight_critical else "false"

                vis_html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
<style>
  html, body {{ margin:0; padding:0; width:100%; height:100%; overflow:hidden; font-family:system-ui,-apple-system,sans-serif; }}
  #cy-container {{ width:100%; height:640px; position:relative; }}
  #tooltip {{
    position:absolute; display:none;
    background:var(--st-color-background, #ffffff);
    border:1px solid rgba(0,0,0,0.12);
    border-radius:8px; padding:10px 14px;
    font-size:13px; font-family:system-ui,-apple-system,sans-serif;
    pointer-events:none; z-index:10;
    max-width:260px; line-height:1.6;
    box-shadow:0 2px 8px rgba(0,0,0,0.08);
  }}
  #legend {{
    display:flex; gap:14px; font-size:12px; padding:8px 12px;
    flex-wrap:wrap; align-items:center;
  }}
</style>
</head><body>
<div id="cy-container"></div>
<div id="tooltip"></div>
<div id="legend"></div>
<script>
(function() {{
  var dk = matchMedia('(prefers-color-scheme: dark)').matches;

  var COLORS = {{
    startEnd: {{
      bg: dk ? '#042C53' : '#E6F1FB',
      border: dk ? '#85B7EB' : '#185FA5',
      text: dk ? '#B5D4F4' : '#042C53'
    }},
    start: {{
      bg: dk ? '#04342C' : '#E1F5EE',
      border: dk ? '#5DCAA5' : '#0F6E56',
      text: dk ? '#9FE1CB' : '#04342C'
    }},
    end: {{
      bg: dk ? '#4A1B0C' : '#FAECE7',
      border: dk ? '#F0997B' : '#993C1D',
      text: dk ? '#F5C4B3' : '#4A1B0C'
    }},
    mid: {{
      bg: dk ? '#2C2C2A' : '#F1EFE8',
      border: dk ? '#B4B2A9' : '#5F5E5A',
      text: dk ? '#D3D1C7' : '#2C2C2A'
    }}
  }};

  var edgeBase = dk ? 'rgba(160,158,150,0.55)' : 'rgba(95,94,90,0.4)';

  function durColor(d, minD, maxD) {{
    if (maxD <= minD) return 'rgb(15,110,166)';
    var t = (d - minD) / (maxD - minD);
    var r, g, b;
    if (t < 0.5) {{
      var u = t * 2;
      r = Math.round(15 + (186 - 15) * u);
      g = Math.round(110 + (117 - 110) * u);
      b = Math.round(166 + (23 - 166) * u);
    }} else {{
      var u = (t - 0.5) * 2;
      r = Math.round(186 + (153 - 186) * u);
      g = Math.round(117 + (60 - 117) * u);
      b = Math.round(23 + (13 - 23) * u);
    }}
    return 'rgb(' + r + ',' + g + ',' + b + ')';
  }}

  var rawNodes = {nodes_json};
  var rawEdges = {edges_json};
  var showDuration = {show_dur_js};
  var highlightCritical = {highlight_crit_js};
  var rankDir = '{rank_dir}';

  var durations = rawEdges.map(function(e) {{ return e.avgDur; }}).filter(function(d) {{ return d != null; }});
  var minDur = durations.length ? Math.min.apply(null, durations) : 0;
  var maxDur = durations.length ? Math.max.apply(null, durations) : 0;

  function fmtDur(s) {{
    if (s == null) return '';
    s = Math.abs(s);
    if (s < 60) return Math.round(s) + '秒';
    var m = Math.floor(s / 60), sec = Math.round(s % 60);
    if (s < 3600) return sec > 0 ? m + '分' + sec + '秒' : m + '分';
    var h = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
    return h + '時間' + mm + '分';
  }}

  var elements = [];

  rawNodes.forEach(function(n) {{
    var c = COLORS[n.nodeType];
    elements.push({{ data: {{
      id: n.id,
      label: n.label,
      bgColor: c.bg,
      borderColor: c.border,
      textColor: c.text,
      count: n.count,
      nodeType: n.nodeType
    }}}});
  }});

  rawEdges.forEach(function(e, i) {{
    var edgeColor = edgeBase;
    var w = e.width;
    var label = '' + e.count;

    if (showDuration && e.avgDur != null) {{
      edgeColor = durColor(e.avgDur, minDur, maxDur);
      label = e.count + ' (' + fmtDur(e.avgDur) + ')';
    }}

    if (highlightCritical) {{
      if (e.isCritical) {{
        w = Math.min(w + 3, 12);
      }} else {{
        edgeColor = dk ? 'rgba(80,80,78,0.3)' : '#DDDDDD';
        w = Math.max(1, w * 0.5);
      }}
    }}

    elements.push({{ data: {{
      id: 'e' + i,
      source: e.source,
      target: e.target,
      count: e.count,
      avgDur: e.avgDur,
      width: w,
      edgeColor: edgeColor,
      edgeLabel: label,
      isCritical: e.isCritical
    }}}});
  }});

  var cyStyle = [
    {{
      selector: 'node',
      style: {{
        'label': 'data(label)',
        'text-wrap': 'wrap',
        'text-valign': 'center',
        'text-halign': 'center',
        'font-size': '12px',
        'font-family': 'system-ui, -apple-system, sans-serif',
        'font-weight': 500,
        'color': 'data(textColor)',
        'background-color': 'data(bgColor)',
        'border-color': 'data(borderColor)',
        'border-width': 1.5,
        'shape': 'roundrectangle',
        'width': 'label',
        'height': 'label',
        'padding': '14px',
        'text-max-width': '120px'
      }}
    }},
    {{
      selector: 'node:active',
      style: {{ 'overlay-opacity': 0.08 }}
    }},
    {{
      selector: 'edge',
      style: {{
        'width': 'data(width)',
        'line-color': 'data(edgeColor)',
        'target-arrow-color': 'data(edgeColor)',
        'target-arrow-shape': 'triangle',
        'arrow-scale': 0.8,
        'curve-style': 'bezier',
        'label': 'data(edgeLabel)',
        'font-size': '10px',
        'font-family': 'system-ui, -apple-system, sans-serif',
        'color': dk ? '#B4B2A9' : '#5F5E5A',
        'text-background-color': dk ? '#1a1a18' : '#ffffff',
        'text-background-opacity': 0.85,
        'text-background-padding': '3px',
        'text-background-shape': 'roundrectangle',
        'edge-text-rotation': 'autorotate',
        'text-margin-y': -8
      }}
    }},
    {{
      selector: 'edge:active',
      style: {{ 'overlay-opacity': 0.06 }}
    }}
  ];

  var cy = cytoscape({{
    container: document.getElementById('cy-container'),
    elements: elements,
    style: cyStyle,
    layout: {{
      name: 'dagre',
      rankDir: rankDir,
      nodeSep: rankDir === 'TB' ? 70 : 60,
      rankSep: rankDir === 'TB' ? 100 : 120,
      edgeSep: 30,
      animate: false,
      padding: 30
    }},
    userZoomingEnabled: true,
    userPanningEnabled: true,
    boxSelectionEnabled: false
  }});

  // ツールチップ
  var tooltip = document.getElementById('tooltip');

  cy.on('mouseover', 'node', function(e) {{
    var d = e.target.data();
    var typeLabel = {{
      startEnd: '開始+終了', start: '開始', end: '終了', mid: '中間'
    }}[d.nodeType];
    tooltip.innerHTML =
      '<div style="font-weight:500;margin-bottom:4px;">' + d.id + '</div>' +
      '<div style="display:flex;justify-content:space-between;gap:16px;">' +
        '<span style="opacity:0.6;">実行回数</span>' +
        '<span style="font-weight:500;">' + d.count + '回</span>' +
      '</div>' +
      '<div style="display:flex;justify-content:space-between;gap:16px;">' +
        '<span style="opacity:0.6;">種類</span>' +
        '<span style="font-weight:500;">' + typeLabel + '</span>' +
      '</div>';
    if (dk) {{
      tooltip.style.background = '#2C2C2A';
      tooltip.style.color = '#D3D1C7';
      tooltip.style.borderColor = 'rgba(255,255,255,0.12)';
    }}
    tooltip.style.display = 'block';
  }});

  cy.on('mouseover', 'edge', function(e) {{
    var d = e.target.data();
    tooltip.innerHTML =
      '<div style="font-weight:500;margin-bottom:4px;">' + d.source + ' → ' + d.target + '</div>' +
      '<div style="display:flex;justify-content:space-between;gap:16px;">' +
        '<span style="opacity:0.6;">頻度</span>' +
        '<span style="font-weight:500;">' + d.count + '回</span>' +
      '</div>' +
      '<div style="display:flex;justify-content:space-between;gap:16px;">' +
        '<span style="opacity:0.6;">平均所要時間</span>' +
        '<span style="font-weight:500;">' + (d.avgDur != null ? fmtDur(d.avgDur) : 'N/A') + '</span>' +
      '</div>';
    if (dk) {{
      tooltip.style.background = '#2C2C2A';
      tooltip.style.color = '#D3D1C7';
      tooltip.style.borderColor = 'rgba(255,255,255,0.12)';
    }}
    tooltip.style.display = 'block';
    e.target.style({{ 'width': Math.max(d.width * 1.8, 3), 'z-index': 999 }});
  }});

  cy.on('mousemove', function(e) {{
    if (tooltip.style.display === 'block') {{
      var rect = document.getElementById('cy-container').getBoundingClientRect();
      var px = e.originalEvent.clientX - rect.left + 12;
      var py = e.originalEvent.clientY - rect.top - 10;
      tooltip.style.left = Math.min(px, rect.width - 270) + 'px';
      tooltip.style.top = py + 'px';
    }}
  }});

  cy.on('mouseout', 'node, edge', function(e) {{
    tooltip.style.display = 'none';
    if (e.target.isEdge()) {{
      e.target.style({{ 'width': e.target.data('width') }});
    }}
  }});

  // 凡例
  var legend = document.getElementById('legend');
  var legendColor = dk ? '#D3D1C7' : '#2C2C2A';
  legend.style.color = legendColor;
  legend.innerHTML =
    '<span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + COLORS.start.border + ';margin-right:3px;vertical-align:middle;"></span> 開始</span>' +
    '<span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + COLORS.end.border + ';margin-right:3px;vertical-align:middle;"></span> 終了</span>' +
    '<span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + COLORS.startEnd.border + ';margin-right:3px;vertical-align:middle;"></span> 開始+終了</span>' +
    '<span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + COLORS.mid.border + ';margin-right:3px;vertical-align:middle;"></span> 中間</span>' +
    '<span>| ホバーで詳細表示 ・ ノードはドラッグ移動可</span>';
}})();
</script>
</body></html>"""
                components.html(vis_html, height=700, scrolling=False)

        except requests.exceptions.HTTPError as e:
            detail = e.response.json().get("detail", str(e)) if e.response else str(e)
            st.warning(detail)
        except Exception as e:
            st.error(f"通信エラー: {e}")


# ===== ページ3: タスクマイニング =====

elif page == "タスクマイニング":
    st.header("タスクマイニング")

    processes = get_processes()
    if not processes:
        st.info("プロセスが登録されていません。先にCSVをアップロードしてください。")
    else:
        proc_options = {p["process_name"]: p["process_id"] for p in processes}
        selected_proc = st.sidebar.selectbox("プロセス選択", list(proc_options.keys()), key="task_proc")
        task_process_id = proc_options[selected_proc]

        tab_def, tab_tag = st.tabs(["タスク定義", "イベントタグ付け"])

        # --- Tab 1: タスク定義管理 ---
        with tab_def:
            st.subheader("タスク定義")

            # 既存タスク定義一覧
            try:
                defs_resp = requests.get(
                    f"{API_URL}/api/v1/tasks/definitions",
                    params={"process_id": task_process_id}, timeout=10,
                )
                defs_resp.raise_for_status()
                task_defs = defs_resp.json()
            except Exception:
                task_defs = []

            if task_defs:
                for td in task_defs:
                    col1, col2, col3, col4 = st.columns([1, 3, 2, 1])
                    color_badge = f'<span style="background:{td["color"] or "#78909C"}; color:white; padding:2px 8px; border-radius:4px;">{td["task_name"]}</span>'
                    col1.markdown(color_badge, unsafe_allow_html=True)
                    col2.text(td.get("description") or "")
                    col3.text(f"ID: {td['task_id']}")
                    if col4.button("削除", key=f"del_def_{td['task_id']}"):
                        try:
                            del_resp = requests.delete(
                                f"{API_URL}/api/v1/tasks/definitions/{td['task_id']}", timeout=10,
                            )
                            del_resp.raise_for_status()
                            st.success("削除しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"削除エラー: {e}")
            else:
                st.info("タスク定義がありません。下のフォームから作成してください。")

            st.divider()
            st.subheader("新規タスク定義")
            with st.form("new_task_def"):
                new_name = st.text_input("タスク名", placeholder="例: 受注処理")
                new_desc = st.text_input("説明（任意）", placeholder="例: メール確認からスプレッドシート入力まで")
                new_color = st.color_picker("色", value="#4CAF50")
                submitted = st.form_submit_button("作成")
                if submitted and new_name:
                    try:
                        resp = requests.post(
                            f"{API_URL}/api/v1/tasks/definitions",
                            json={
                                "process_id": task_process_id,
                                "task_name": new_name,
                                "description": new_desc or None,
                                "color": new_color,
                            },
                            timeout=10,
                        )
                        resp.raise_for_status()
                        st.success(resp.json().get("message", "作成しました"))
                        st.rerun()
                    except requests.exceptions.HTTPError as e:
                        detail = e.response.json().get("detail", str(e)) if e.response else str(e)
                        st.error(detail)

        # --- Tab 2: イベントタグ付け ---
        with tab_tag:
            st.subheader("イベントタグ付け")

            # タスク定義がなければ警告
            if not task_defs:
                try:
                    defs_resp2 = requests.get(
                        f"{API_URL}/api/v1/tasks/definitions",
                        params={"process_id": task_process_id}, timeout=10,
                    )
                    defs_resp2.raise_for_status()
                    task_defs = defs_resp2.json()
                except Exception:
                    task_defs = []

            if not task_defs:
                st.warning("先に「タスク定義」タブでタスクを定義してください。")
            else:
                # ケース一覧取得
                try:
                    cases_resp = requests.get(
                        f"{API_URL}/api/v1/tasks/cases",
                        params={"process_id": task_process_id}, timeout=10,
                    )
                    cases_resp.raise_for_status()
                    cases = cases_resp.json()
                except Exception:
                    cases = []

                if not cases:
                    st.info("イベントデータがありません。")
                else:
                    case_labels = {
                        f"{c['case_id']} ({c['tagged_count']}/{c['event_count']}タグ済)": c["case_id"]
                        for c in cases
                    }
                    selected_case_label = st.selectbox("ケース選択", list(case_labels.keys()))
                    selected_case_id = case_labels[selected_case_label]

                    # 選択ケースのタグ率
                    case_info = next(c for c in cases if c["case_id"] == selected_case_id)
                    tag_pct = round(case_info["tagged_count"] / case_info["event_count"] * 100) if case_info["event_count"] > 0 else 0
                    st.metric("タグ率", f"{tag_pct}% ({case_info['tagged_count']}/{case_info['event_count']})")

                    # イベント一覧取得
                    try:
                        events_resp = requests.get(
                            f"{API_URL}/api/v1/tasks/events",
                            params={"process_id": task_process_id, "case_id": selected_case_id},
                            timeout=10,
                        )
                        events_resp.raise_for_status()
                        events_data = events_resp.json()
                    except Exception:
                        events_data = []

                    if events_data:
                        # イベントテーブル表示（色付き）
                        df_events = pd.DataFrame(events_data)
                        display_df = df_events[["row_num", "activity_name", "event_timestamp", "source_system", "task_name"]].copy()
                        display_df.columns = ["#", "Activity", "Timestamp", "Source", "タスク"]
                        display_df["タスク"] = display_df["タスク"].fillna("(未分類)")

                        # 色付きスタイル
                        task_colors = {td["task_name"]: td.get("color", "#78909C") for td in task_defs}

                        def highlight_task(row):
                            task = row["タスク"]
                            if task == "(未分類)":
                                return [""] * len(row)
                            color = task_colors.get(task, "#78909C")
                            return [f"background-color: {color}22; border-left: 3px solid {color}"] * len(row)

                        styled = display_df.style.apply(highlight_task, axis=1)
                        st.dataframe(styled, use_container_width=True, hide_index=True, height=400)

                        # --- タグ付けフォーム ---
                        st.divider()
                        st.subheader("タグ付け")
                        col_start, col_end, col_task = st.columns(3)
                        max_row = len(events_data)
                        start_row = col_start.number_input("開始行 #", min_value=1, max_value=max_row, value=1, key="tag_start")
                        end_row = col_end.number_input("終了行 #", min_value=1, max_value=max_row, value=min(2, max_row), key="tag_end")
                        task_options = {td["task_name"]: td["task_id"] for td in task_defs}
                        selected_task_name = col_task.selectbox("タスク", list(task_options.keys()), key="tag_task")

                        if st.button("タグ付け"):
                            if start_row > end_row:
                                st.error("開始行は終了行以下にしてください")
                            else:
                                ev_start = events_data[start_row - 1]["event_id"]
                                ev_end = events_data[end_row - 1]["event_id"]
                                try:
                                    tag_resp = requests.post(
                                        f"{API_URL}/api/v1/tasks/tag",
                                        json={
                                            "task_id": task_options[selected_task_name],
                                            "case_id": selected_case_id,
                                            "process_id": task_process_id,
                                            "event_id_start": ev_start,
                                            "event_id_end": ev_end,
                                        },
                                        timeout=10,
                                    )
                                    tag_resp.raise_for_status()
                                    st.success(tag_resp.json().get("message", "タグ付けしました"))
                                    st.rerun()
                                except requests.exceptions.HTTPError as e:
                                    detail = e.response.json().get("detail", str(e)) if e.response else str(e)
                                    st.error(detail)

                        # --- タグ済み一覧 ---
                        tagged_events = [e for e in events_data if e["task_name"] is not None]
                        if tagged_events:
                            st.divider()
                            st.subheader("タグ済み一覧")
                            # タスクインスタンスごとにグループ表示
                            try:
                                tags_resp = requests.get(
                                    f"{API_URL}/api/v1/tasks/tag",
                                    params={"process_id": task_process_id, "case_id": selected_case_id},
                                    timeout=10,
                                )
                                tags_resp.raise_for_status()
                                tags_list = tags_resp.json()
                            except Exception:
                                tags_list = []

                            for tag in tags_list:
                                col_info, col_del = st.columns([5, 1])
                                color = tag.get("color", "#78909C")
                                badge = f'<span style="background:{color}; color:white; padding:2px 8px; border-radius:4px;">{tag["task_name"]}</span>'
                                # 行番号を逆引き
                                start_idx = next((e["row_num"] for e in events_data if e["event_id"] == tag["event_id_start"]), "?")
                                end_idx = next((e["row_num"] for e in events_data if e["event_id"] == tag["event_id_end"]), "?")
                                col_info.markdown(
                                    f'{badge} &nbsp; #{start_idx}〜#{end_idx} ({tag["event_count"]}件)',
                                    unsafe_allow_html=True,
                                )
                                if col_del.button("解除", key=f"untag_{tag['task_instance_id']}"):
                                    try:
                                        del_resp = requests.delete(
                                            f"{API_URL}/api/v1/tasks/tag/{tag['task_instance_id']}", timeout=10,
                                        )
                                        del_resp.raise_for_status()
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"解除エラー: {e}")


# ===== ページ4: プロセスマップα =====

elif page == "プロセスマップα":
    st.header("プロセスマップα")

    processes = get_processes()
    if not processes:
        st.info("プロセスが登録されていません。先にCSVをアップロードしてください。")
    else:
        proc_options = {p["process_name"]: p["process_id"] for p in processes}
        selected_proc = st.sidebar.selectbox("プロセス選択", list(proc_options.keys()), key="map_proc")
        map_process_id = proc_options[selected_proc]

        # --- マップ一覧取得 ---
        try:
            maps_resp = requests.get(
                f"{API_URL}/api/v1/maps", params={"process_id": map_process_id}, timeout=10
            )
            maps_resp.raise_for_status()
            maps_list = maps_resp.json()
        except Exception:
            maps_list = []

        st.sidebar.divider()
        st.sidebar.subheader("マップ操作")

        # --- DFGをマップとして保存 ---
        dfg_map_name = st.sidebar.text_input("マップ名", value="default", key="dfg_map_name")
        if st.sidebar.button("DFGをマップとして保存"):
            try:
                resp = requests.post(
                    f"{API_URL}/api/v1/maps/from-dfg",
                    json={"process_id": map_process_id, "map_name": dfg_map_name},
                    timeout=30,
                )
                resp.raise_for_status()
                st.sidebar.success(resp.json().get("message", "保存しました"))
                st.rerun()
            except requests.exceptions.HTTPError as e:
                detail = e.response.json().get("detail", str(e)) if e.response else str(e)
                st.sidebar.error(detail)

        # --- インポート ---
        st.sidebar.divider()
        st.sidebar.subheader("インポート")
        import_file = st.sidebar.file_uploader("JSON / CSV ファイル", type=["json", "csv"], key="map_import")
        import_map_name = st.sidebar.text_input("インポート先マップ名", value="imported", key="import_name")
        if st.sidebar.button("インポート実行") and import_file:
            try:
                files = {"file": (import_file.name, import_file.getvalue(), "application/octet-stream")}
                data = {"process_id": str(map_process_id), "map_name": import_map_name}
                resp = requests.post(
                    f"{API_URL}/api/v1/maps/import", files=files, data=data, timeout=30
                )
                resp.raise_for_status()
                st.sidebar.success(resp.json().get("message", "インポートしました"))
                st.rerun()
            except requests.exceptions.HTTPError as e:
                detail = e.response.json().get("detail", str(e)) if e.response else str(e)
                st.sidebar.error(detail)

        # --- マップ選択 & 表示 ---
        if not maps_list:
            st.info("保存済みマップがありません。「DFGをマップとして保存」またはファイルをインポートしてください。")
        else:
            map_options = {f"{m['map_name']} ({m['source']})": m["map_id"] for m in maps_list}
            selected_map_label = st.selectbox("マップ選択", list(map_options.keys()))
            selected_map_id = map_options[selected_map_label]

            # マップデータ取得
            try:
                map_resp = requests.get(f"{API_URL}/api/v1/maps/{selected_map_id}", timeout=10)
                map_resp.raise_for_status()
                map_data = map_resp.json()
            except Exception as e:
                st.error(f"マップ取得エラー: {e}")
                map_data = None

            if map_data:
                map_nodes = map_data["nodes"]
                map_edges = map_data["edges"]

                # --- エクスポート ---
                col_exp1, col_exp2, col_exp3 = st.columns([1, 1, 2])
                with col_exp1:
                    export_json = json.dumps({
                        "format_version": "1.0",
                        "process_name": selected_proc,
                        "map_name": map_data["map_name"],
                        "nodes": map_nodes,
                        "edges": map_edges,
                        "metadata": map_data.get("metadata"),
                    }, ensure_ascii=False, indent=2)
                    st.download_button(
                        "JSONエクスポート",
                        data=export_json,
                        file_name=f"map_{selected_map_id}.json",
                        mime="application/json",
                    )
                with col_exp2:
                    # CSVエクスポート
                    csv_buf = io.StringIO()
                    writer = csv.writer(csv_buf)
                    writer.writerow(["From", "To", "Label"])
                    for edge in map_edges:
                        writer.writerow([edge.get("from", ""), edge.get("to", ""), edge.get("label", "")])
                    st.download_button(
                        "CSVエクスポート",
                        data=csv_buf.getvalue(),
                        file_name=f"map_{selected_map_id}.csv",
                        mime="text/csv",
                    )
                with col_exp3:
                    if st.button("このマップを削除", type="secondary"):
                        try:
                            del_resp = requests.delete(f"{API_URL}/api/v1/maps/{selected_map_id}", timeout=10)
                            del_resp.raise_for_status()
                            st.success("マップを削除しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"削除エラー: {e}")

                # --- vis.js 編集可能キャンバス ---
                node_colors = {
                    "start": {"background": "#4CAF50", "border": "#2E7D32"},
                    "end": {"background": "#F44336", "border": "#C62828"},
                    "both": {"background": "#FF9800", "border": "#E65100"},
                    "intermediate": {"background": "#78909C", "border": "#546E7A"},
                }

                vis_nodes = []
                for n in map_nodes:
                    ntype = n.get("type", "intermediate")
                    color = node_colors.get(ntype, node_colors["intermediate"])
                    freq = n.get("frequency")
                    label = n.get("label", n.get("id", ""))
                    if freq:
                        label = f"{label}\n({freq})"
                    vis_nodes.append({
                        "id": n.get("id", n.get("label")),
                        "label": label,
                        "x": n.get("x", 0),
                        "y": n.get("y", 0),
                        "color": color,
                        "shape": "box",
                        "borderWidth": 2,
                        "font": {"size": 14, "color": "#FFFFFF"},
                        "type": ntype,
                    })

                vis_edges = []
                for e in map_edges:
                    elabel = e.get("label", "")
                    freq = e.get("frequency")
                    avg_dur = e.get("avg_duration_sec")
                    if freq and not elabel:
                        parts = [str(freq)]
                        if avg_dur is not None:
                            parts.append(f"({format_duration(avg_dur)})")
                        elabel = " ".join(parts)
                    vis_edges.append({
                        "id": e.get("id", f"{e.get('from', '')}_{e.get('to', '')}"),
                        "from": e.get("from", ""),
                        "to": e.get("to", ""),
                        "label": elabel,
                        "arrows": "to",
                        "color": {"color": "#999999"},
                        "font": {"size": 11, "align": "horizontal", "background": "white"},
                        "smooth": {"type": "curvedCW", "roundness": 0.15},
                    })

                nodes_json = json.dumps(vis_nodes, ensure_ascii=False)
                edges_json = json.dumps(vis_edges, ensure_ascii=False)

                vis_html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  html, body {{ margin:0; padding:0; width:100%; height:100%; overflow:hidden; font-family:sans-serif; }}
  #graph {{ width:100%; height:580px; border:1px solid #ddd; }}
  #toolbar {{ padding:8px; display:flex; gap:8px; align-items:center; }}
  #toolbar button {{ padding:6px 16px; border:1px solid #ccc; border-radius:4px; cursor:pointer;
                     background:#fff; font-size:13px; }}
  #toolbar button:hover {{ background:#f0f0f0; }}
  #toolbar button.primary {{ background:#1976D2; color:#fff; border-color:#1565C0; }}
  #toolbar button.primary:hover {{ background:#1565C0; }}
  #toast {{ position:fixed; top:12px; right:12px; padding:10px 20px; border-radius:6px;
            color:#fff; font-size:14px; display:none; z-index:9999; }}
  #toast.success {{ background:#4CAF50; }}
  #toast.error {{ background:#F44336; }}
</style>
</head><body>
<div id="toolbar">
  <button class="primary" onclick="saveMap()">💾 保存</button>
  <button onclick="exportJSON()">📥 JSONエクスポート</button>
  <span style="color:#888; font-size:12px;">ツールバーの「ノード追加」「エッジ追加」「削除」で編集 / ダブルクリックで名前変更</span>
</div>
<div id="graph"></div>
<div id="toast"></div>
<script>
var PUBLIC_API_URL = "{PUBLIC_API_URL}";
var PROCESS_ID = {map_process_id};
var MAP_NAME = "{map_data['map_name']}";
var MAP_ID = {selected_map_id};

var nodes = new vis.DataSet({nodes_json});
var edges = new vis.DataSet({edges_json});
var container = document.getElementById("graph");

var options = {{
  manipulation: {{
    enabled: true,
    addNode: function(nodeData, callback) {{
      var name = prompt("ノード名を入力:", "新規アクティビティ");
      if (name) {{
        nodeData.id = name;
        nodeData.label = name;
        nodeData.shape = "box";
        nodeData.color = {{background: "#78909C", border: "#546E7A"}};
        nodeData.font = {{size: 14, color: "#FFFFFF"}};
        nodeData.borderWidth = 2;
        nodeData.type = "intermediate";
        callback(nodeData);
      }}
    }},
    editNode: function(nodeData, callback) {{
      var newName = prompt("ノード名を変更:", nodeData.label.split("\\n")[0]);
      if (newName !== null) {{
        nodeData.label = newName;
        nodeData.id = newName;
        callback(nodeData);
      }} else {{
        callback(null);
      }}
    }},
    addEdge: function(edgeData, callback) {{
      if (edgeData.from !== edgeData.to) {{
        edgeData.arrows = "to";
        edgeData.color = {{color: "#999999"}};
        edgeData.font = {{size: 11, align: "horizontal", background: "white"}};
        edgeData.smooth = {{type: "curvedCW", roundness: 0.15}};
        edgeData.id = edgeData.from + "_" + edgeData.to + "_" + Date.now();
        callback(edgeData);
      }}
    }},
    deleteNode: function(data, callback) {{
      if (confirm("選択したノードを削除しますか？")) callback(data);
      else callback(null);
    }},
    deleteEdge: function(data, callback) {{
      if (confirm("選択したエッジを削除しますか？")) callback(data);
      else callback(null);
    }}
  }},
  physics: {{ enabled: false }},
  interaction: {{ hover: true, zoomView: true, dragView: true, dragNodes: true, tooltipDelay: 100 }}
}};

var network = new vis.Network(container, {{nodes: nodes, edges: edges}}, options);
network.once("afterDrawing", function() {{ network.fit({{ animation: false }}); }});

function showToast(msg, type) {{
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.className = type;
  t.style.display = "block";
  setTimeout(function() {{ t.style.display = "none"; }}, 3000);
}}

function getGraphData() {{
  var positions = network.getPositions();
  var nodeData = nodes.get().map(function(n) {{
    var pos = positions[n.id] || {{x: n.x || 0, y: n.y || 0}};
    return {{
      id: n.id,
      label: (n.label || "").split("\\n")[0],
      x: Math.round(pos.x),
      y: Math.round(pos.y),
      type: n.type || "intermediate"
    }};
  }});
  var edgeData = edges.get().map(function(e) {{
    return {{
      id: e.id,
      from: e.from,
      to: e.to,
      label: e.label || ""
    }};
  }});
  return {{nodes: nodeData, edges: edgeData}};
}}

function saveMap() {{
  var data = getGraphData();
  fetch(PUBLIC_API_URL + "/api/v1/maps", {{
    method: "POST",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{
      process_id: PROCESS_ID,
      map_name: MAP_NAME,
      source: "manual",
      nodes: data.nodes,
      edges: data.edges
    }})
  }}).then(function(r) {{ return r.json(); }})
    .then(function(res) {{ showToast(res.message || "保存しました", "success"); }})
    .catch(function(err) {{ showToast("保存エラー: " + err, "error"); }});
}}

function exportJSON() {{
  var data = getGraphData();
  var exportObj = {{
    format_version: "1.0",
    process_name: "{selected_proc}",
    map_name: MAP_NAME,
    nodes: data.nodes,
    edges: data.edges,
    metadata: {{}}
  }};
  var blob = new Blob([JSON.stringify(exportObj, null, 2)], {{type: "application/json"}});
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "map_" + MAP_ID + ".json";
  a.click();
}}
</script>
</body></html>"""
                components.html(vis_html, height=650, scrolling=False)

                st.markdown(
                    """
                    <div style="display:flex; gap:16px; flex-wrap:wrap; padding:8px 0; font-size:14px;">
                        <span><span style="background:#4CAF50; color:white; padding:2px 8px; border-radius:4px;">■</span> 開始</span>
                        <span><span style="background:#F44336; color:white; padding:2px 8px; border-radius:4px;">■</span> 終了</span>
                        <span><span style="background:#FF9800; color:white; padding:2px 8px; border-radius:4px;">■</span> 開始+終了</span>
                        <span><span style="background:#78909C; color:white; padding:2px 8px; border-radius:4px;">■</span> 中間</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# ===== ページ4: KPIダッシュボード =====

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
