import os

import plotly.express as px
import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="NADJA PM", page_icon="📊", layout="wide")
st.sidebar.title("NADJA Process Mining")

page = st.sidebar.radio("ページ選択", ["CSVアップロード", "プロセスマップ", "KPIダッシュボード"])


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

    if st.button("アップロード", disabled=not (uploaded_file and process_name)):
        with st.spinner("インポート中..."):
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")}
            data = {"process_name": process_name}
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

            # DFG JSON → Graphviz DOT
            start_acts = set(dfg.get("start_activities", []))
            end_acts = set(dfg.get("end_activities", []))

            max_edge_count = max((e["count"] for e in dfg["edges"]), default=1)

            dot_lines = [
                "digraph {",
                '  rankdir=LR;',
                '  node [shape=box, style=filled, fillcolor="#E8E8E8", fontname="sans-serif"];',
                '  edge [fontname="sans-serif"];',
            ]

            for node in dfg["nodes"]:
                name = node["name"]
                count = node["count"]
                if name in start_acts and name in end_acts:
                    color = "#FFD700"  # 金: 開始かつ終了
                elif name in start_acts:
                    color = "#90EE90"  # 緑: 開始
                elif name in end_acts:
                    color = "#FFB6C1"  # ピンク: 終了
                else:
                    color = "#E8E8E8"
                label = f"{name}\\n({count})"
                dot_lines.append(
                    f'  "{name}" [label="{label}", fillcolor="{color}"];'
                )

            for edge in dfg["edges"]:
                pw = max(1.0, 5.0 * edge["count"] / max_edge_count)
                label = str(edge["count"])
                if edge.get("avg_duration_sec") is not None:
                    label += f"\\n({edge['avg_duration_sec']}s)"
                dot_lines.append(
                    f'  "{edge["from"]}" -> "{edge["to"]}" [label="{label}", penwidth={pw:.1f}];'
                )

            dot_lines.append("}")
            dot_string = "\n".join(dot_lines)

            st.graphviz_chart(dot_string, use_container_width=True)

            # 凡例
            st.caption("🟢 緑=開始アクティビティ　🔴 ピンク=終了アクティビティ　🟡 金=開始かつ終了")

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
            minutes = int(avg_dur // 60)
            seconds = int(avg_dur % 60)
            dur_display = f"{minutes}分{seconds}秒" if minutes else f"{seconds}秒"

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
                st.dataframe(
                    var_data["variants"],
                    column_config={
                        "variant": "バリアント",
                        "count": "件数",
                        "percentage": st.column_config.NumberColumn("割合(%)", format="%.1f"),
                        "avg_duration_sec": st.column_config.NumberColumn("平均所要時間(秒)", format="%.1f"),
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
