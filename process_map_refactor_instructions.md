# プロセスマップ描画エンジン改修指示書

## 概要

`streamlit/app.py` のページ2「プロセスマップ（DFG）」の描画エンジンを **vis.js → Cytoscape.js + Dagre** に全面置換する。  
バックエンド（`api/app/pm_engine.py`、APIレスポンス構造）は変更しない。フロントエンド側のみの改修。

---

## 背景と課題

現行実装の問題点：

1. **レイアウト**: BFS + 固定座標（L182〜218）で交差最小化なし → 線が重なる
2. **ライブラリ**: vis-network v9.1.9 はメンテ停滞（2023年〜）
3. **双方向エッジ**: `curvedCW` 固定値で重なり発生（L295〜300）
4. **情報過多**: 全エッジに `count (duration)` ラベル表示（L284〜287）
5. **カラー**: Material Design 直値ハードコード、ダークモード非対応（L221〜226）
6. **凡例**: `st.markdown` でHTML直書き（L356〜367）

※初期設計書ではGraphviz指定だったが、インタラクティブ操作のためvis.jsに変更された経緯あり。Graphvizは静的画像でズーム・ドラッグ不可のため不採用。

---

## 改修方針

### 削除するもの（app.py）

以下のコードブロックをすべて削除：

- **L38〜52**: `duration_color()` 関数 → JS側で処理
- **L182〜218**: BFSレベル計算 + 固定座標計算（`adjacency`, `node_levels`, `queue`, `level_groups`, `LEVEL_SEP`, `NODE_SEP`, `node_positions` すべて）
- **L220〜261**: vis.js ノードデータ構築（`node_colors`, `vis_nodes`）
- **L263〜312**: vis.js エッジデータ構築（`edge_set`, `vis_edges`, `duration_color` 呼び出し）
- **L314〜353**: vis.js HTML生成 + `components.html` 呼び出し
- **L355〜367**: 凡例の `st.markdown`
- **L4**: `from collections import deque`（BFS用だったので不要になる）

### 残すもの（app.py）

- **L20〜35**: `format_duration()` → KPIダッシュボードでも使用
- **L118〜145**: プロセス選択、DFG API呼び出し、`start_acts` / `end_acts` / `edges` の取得
- **L146〜170**: サイドバーのフィルタ設定（最小頻度、所要時間表示、クリティカルパス）
- **L156〜180**: フィルタ適用ロジック（`filtered_edges`, `critical_set`, `active_nodes`）

### 追加するサイドバーコントロール

既存のフィルタ設定セクション（L147〜154付近）に以下を追加：

```python
layout_direction = st.sidebar.radio(
    "レイアウト方向", ["上→下", "左→右"], horizontal=True
)
```

---

## 新しい描画エンジンの実装

削除したvis.js HTML生成部分（L314〜353）を、以下のCytoscape.js + Dagre実装に置換する。

### Python側（app.py の該当箇所）

```python
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
```

### HTML テンプレート（components.html に渡す文字列）

以下のHTMLテンプレートをf-stringで生成し、`components.html(vis_html, height=700, scrolling=False)` で埋め込む。

CDN:
- `https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js`
- `https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js`
- `https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js`

---

### ノード色のカラーテーマ

Streamlit のダークモード検出は `matchMedia('(prefers-color-scheme: dark)')` で行う。

```javascript
const dk = matchMedia('(prefers-color-scheme: dark)').matches;

const COLORS = {
  startEnd: {
    bg: dk ? '#042C53' : '#E6F1FB',
    border: dk ? '#85B7EB' : '#185FA5',
    text: dk ? '#B5D4F4' : '#042C53'
  },
  start: {
    bg: dk ? '#04342C' : '#E1F5EE',
    border: dk ? '#5DCAA5' : '#0F6E56',
    text: dk ? '#9FE1CB' : '#04342C'
  },
  end: {
    bg: dk ? '#4A1B0C' : '#FAECE7',
    border: dk ? '#F0997B' : '#993C1D',
    text: dk ? '#F5C4B3' : '#4A1B0C'
  },
  mid: {
    bg: dk ? '#2C2C2A' : '#F1EFE8',
    border: dk ? '#B4B2A9' : '#5F5E5A',
    text: dk ? '#D3D1C7' : '#2C2C2A'
  }
};
```

### エッジ色のロジック

デフォルト（頻度モード）はニュートラルグレー。所要時間表示時は青→黄→赤グラデーション。

```javascript
const edgeBase = dk ? 'rgba(160,158,150,0.55)' : 'rgba(95,94,90,0.4)';

function durColor(d, minD, maxD) {
  if (maxD <= minD) return 'rgb(15,110,166)';
  const t = (d - minD) / (maxD - minD);
  let r, g, b;
  if (t < 0.5) {
    const u = t * 2;
    r = Math.round(15 + (186 - 15) * u);
    g = Math.round(110 + (117 - 110) * u);
    b = Math.round(166 + (23 - 166) * u);
  } else {
    const u = (t - 0.5) * 2;
    r = Math.round(186 + (153 - 186) * u);
    g = Math.round(117 + (60 - 117) * u);
    b = Math.round(23 + (13 - 23) * u);
  }
  return `rgb(${r},${g},${b})`;
}
```

### Cytoscape.js のスタイル定義

```javascript
const cyStyle = [
  {
    selector: 'node',
    style: {
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
      'text-max-width': '120px',
    }
  },
  {
    selector: 'node:active',
    style: { 'overlay-opacity': 0.08 }
  },
  {
    selector: 'edge',
    style: {
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
      'text-margin-y': -8,
    }
  },
  {
    selector: 'edge:active',
    style: { 'overlay-opacity': 0.06 }
  }
];
```

### Dagre レイアウト設定

```javascript
const layoutConfig = {
  name: 'dagre',
  rankDir: '{rank_dir}',  // Python変数から注入: 'TB' or 'LR'
  nodeSep: rankDir === 'TB' ? 70 : 60,
  rankSep: rankDir === 'TB' ? 100 : 120,
  edgeSep: 30,
  animate: false,
  padding: 30,
};
```

### Cytoscape elements の構築（JavaScript側）

Python側で作った `cy_nodes` と `cy_edges` のJSON をJS側で受け取り、Cytoscape elements に変換する：

```javascript
const rawNodes = {nodes_json};  // Python f-string で注入
const rawEdges = {edges_json};  // Python f-string で注入
const showDuration = {show_dur_js};
const highlightCritical = {highlight_crit_js};

// 所要時間の min/max 計算
const durations = rawEdges.map(e => e.avgDur).filter(d => d != null);
const minDur = durations.length ? Math.min(...durations) : 0;
const maxDur = durations.length ? Math.max(...durations) : 0;

function fmtDur(s) {
  if (s == null) return '';
  s = Math.abs(s);
  if (s < 60) return Math.round(s) + '秒';
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  if (s < 3600) return sec > 0 ? m + '分' + sec + '秒' : m + '分';
  const h = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  return h + '時間' + mm + '分';
}

const elements = [];

rawNodes.forEach(n => {
  const c = COLORS[n.nodeType];
  elements.push({ data: {
    id: n.id,
    label: n.label,
    bgColor: c.bg,
    borderColor: c.border,
    textColor: c.text,
    count: n.count,
    nodeType: n.nodeType,
  }});
});

rawEdges.forEach((e, i) => {
  let edgeColor = edgeBase;
  let w = e.width;
  let label = '' + e.count;

  // 所要時間表示モード
  if (showDuration && e.avgDur != null) {
    edgeColor = durColor(e.avgDur, minDur, maxDur);
    label = e.count + ' (' + fmtDur(e.avgDur) + ')';
  }

  // クリティカルパス強調
  if (highlightCritical) {
    if (e.isCritical) {
      w = Math.min(w + 3, 12);
    } else {
      edgeColor = dk ? 'rgba(80,80,78,0.3)' : '#DDDDDD';
      w = Math.max(1, w * 0.5);
    }
  }

  elements.push({ data: {
    id: 'e' + i,
    source: e.source,
    target: e.target,
    count: e.count,
    avgDur: e.avgDur,
    width: w,
    edgeColor: edgeColor,
    edgeLabel: label,
    isCritical: e.isCritical,
  }});
});
```

### ツールチップ実装

HTML内に `<div id="tooltip">` を配置し、Cytoscape のマウスイベントで制御する。

```javascript
const tooltip = document.getElementById('tooltip');

cy.on('mouseover', 'node', function(e) {
  const d = e.target.data();
  const typeLabel = {
    startEnd: '開始+終了', start: '開始', end: '終了', mid: '中間'
  }[d.nodeType];
  tooltip.innerHTML = `
    <div style="font-weight:500;margin-bottom:4px;">${d.id}</div>
    <div style="display:flex;justify-content:space-between;gap:16px;">
      <span style="opacity:0.6;">実行回数</span>
      <span style="font-weight:500;">${d.count}回</span>
    </div>
    <div style="display:flex;justify-content:space-between;gap:16px;">
      <span style="opacity:0.6;">種類</span>
      <span style="font-weight:500;">${typeLabel}</span>
    </div>`;
  tooltip.style.display = 'block';
});

cy.on('mouseover', 'edge', function(e) {
  const d = e.target.data();
  tooltip.innerHTML = `
    <div style="font-weight:500;margin-bottom:4px;">${d.source} → ${d.target}</div>
    <div style="display:flex;justify-content:space-between;gap:16px;">
      <span style="opacity:0.6;">頻度</span>
      <span style="font-weight:500;">${d.count}回</span>
    </div>
    <div style="display:flex;justify-content:space-between;gap:16px;">
      <span style="opacity:0.6;">平均所要時間</span>
      <span style="font-weight:500;">${d.avgDur != null ? fmtDur(d.avgDur) : 'N/A'}</span>
    </div>`;
  tooltip.style.display = 'block';
  // ホバー時にエッジを太くする
  e.target.style({ 'width': Math.max(d.width * 1.8, 3), 'z-index': 999 });
});

cy.on('mousemove', function(e) {
  if (tooltip.style.display === 'block') {
    const rect = document.getElementById('cy-container').getBoundingClientRect();
    const px = e.originalEvent.clientX - rect.left + 12;
    const py = e.originalEvent.clientY - rect.top - 10;
    tooltip.style.left = Math.min(px, rect.width - 270) + 'px';
    tooltip.style.top = py + 'px';
  }
});

cy.on('mouseout', 'node, edge', function(e) {
  tooltip.style.display = 'none';
  if (e.target.isEdge()) {
    e.target.style({ 'width': e.target.data('width') });
  }
});
```

### ツールチップ用 CSS

```css
#tooltip {
  position: absolute;
  display: none;
  background: var(--st-color-background, #ffffff);
  border: 1px solid rgba(0,0,0,0.12);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
  font-family: system-ui, -apple-system, sans-serif;
  pointer-events: none;
  z-index: 10;
  max-width: 260px;
  line-height: 1.6;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
```

注意: Streamlit の iframe 内では CSS 変数 `--st-color-*` が使える場合と使えない場合がある。フォールバック値を必ず入れること。ダークモード時は JS の `dk` フラグでインラインスタイルを切り替える方が確実。

### 凡例（vis.js HTML 内に統合）

凡例は `components.html` の中の HTML に含める。`st.markdown` の凡例は削除。

```html
<div id="legend" style="display:flex;gap:14px;font-size:12px;padding:8px 12px;flex-wrap:wrap;align-items:center;">
  <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${開始色};margin-right:3px;vertical-align:middle;"></span> 開始</span>
  <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${終了色};margin-right:3px;vertical-align:middle;"></span> 終了</span>
  <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${開始+終了色};margin-right:3px;vertical-align:middle;"></span> 開始+終了</span>
  <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${中間色};margin-right:3px;vertical-align:middle;"></span> 中間</span>
  <span>| ホバーで詳細表示 ・ ノードはドラッグ移動可</span>
</div>
```

凡例の色は JS の `COLORS` オブジェクトの `border` 値を使う。

---

## 実装の注意点

### 1. f-string のエスケープ

Pythonのf-string内でJavaScriptの `{` `}` を使う場合は `{{` `}}` にエスケープすること。
現行のvis.js HTML（L317〜352）と同じパターン。

### 2. コンテナ高さ

`components.html(html, height=700, scrolling=False)` — 現行650pxから700pxに変更（凡例を内包するため）。

### 3. CDN URLの固定

ネットワーク制約がある環境を考慮し、CDN URLはバージョン固定にする：
- cytoscape: `3.28.1`
- dagre: `0.8.5`
- cytoscape-dagre: `2.5.0`

### 4. `deque` の import 削除

`from collections import deque` は BFS 用だったので不要になる。ただし他の箇所で使っていないか確認すること。

### 5. `duration_color()` 関数

この関数はページ2のみで使用。ページ3（KPIダッシュボード）では使っていないので安全に削除可能。

### 6. サイドバーの既存コントロールとの整合

サイドバーの「最小頻度フィルタ」「所要時間を表示」「クリティカルパス強調」はそのまま残す。  
これらの値は Python 側でフィルタ適用した結果を JSON に入れて JS に渡す設計のため、JS 側でリアルタイムフィルタする必要はない（Streamlitのリアクティブ再描画に任せる）。

新しく「レイアウト方向」ラジオボタンを追加する。

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `streamlit/app.py` | ページ2のvis.js描画を Cytoscape.js + Dagre に全面置換。サイドバーにレイアウト方向を追加 |
| `streamlit/requirements.txt` | 変更なし（JSライブラリはCDN読み込みのため） |
| `api/app/pm_engine.py` | 変更なし |
| `api/app/main.py` | 変更なし |

---

## テスト手順

```bash
docker-compose up --build
```

1. `http://localhost:8501` を開く
2. 「CSVアップロード」で `sample_data/営業事務_sample.csv` をアップロード（プロセス名: 「営業事務」）
3. 「プロセスマップ」ページを開く
4. 以下を確認:
   - [ ] ノードが自動配置され、エッジの交差が現行より大幅に減少していること
   - [ ] ノードのホバーでツールチップ（アクティビティ名、実行回数、種類）が表示されること
   - [ ] エッジのホバーでツールチップ（遷移元→先、頻度、平均所要時間）が表示されること
   - [ ] エッジホバー時に太さが変わること
   - [ ] ノードのドラッグ移動ができること
   - [ ] マウスホイールでズーム、ドラッグでパンできること
   - [ ] サイドバー「レイアウト方向」で「上→下」「左→右」を切り替えると再描画されること
   - [ ] サイドバー「最小頻度フィルタ」でエッジ/ノードがフィルタされること
   - [ ] サイドバー「所要時間を表示」ONでエッジラベルに所要時間が追加され、エッジ色が青→黄→赤になること
   - [ ] サイドバー「クリティカルパス強調」ONで主要パスが太く、他が薄くなること
   - [ ] 凡例が正しく表示されること（開始=ティール、終了=コーラル、開始+終了=ブルー、中間=グレー）
   - [ ] Streamlitのダークモード切替で色が適切に変わること（iframe内のダークモード検出）
5. 「KPIダッシュボード」ページが影響なく動作すること
