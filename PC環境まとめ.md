# PC開発環境まとめ（2026-03-23 時点）

PC初期化前のスナップショット。

---

## ランタイム・言語

| ツール | バージョン | 備考 |
|---|---|---|
| Node.js | 24.14.0 | npm 11.9.0 同梱 |
| .NET SDK 10 | 10.0.103 | 最新 |
| .NET SDK 8 | 8.0.419 | LTS |
| Python | **未インストール** | pip も無し |

## 開発ツール

| ツール | バージョン | 備考 |
|---|---|---|
| Git | 2.53.0 | Windows版 |
| GitHub CLI (gh) | 2.87.3 | |
| VS Code | 1.112.0 | ユーザーインストール |
| Kiro (Amazon IDE) | 0.10.32 | |
| Docker Desktop | 4.64.0 | WSL2バックエンド |
| WSL | 2.6.3 | docker-desktop ディストロのみ |
| Ollama | 0.18.2 | ローカルLLM |
| Graphviz | 14.1.3 | グラフ描画 |
| サクラエディタ | 2.4.2.6048 | |

## グローバル npm パッケージ

| パッケージ | バージョン |
|---|---|
| @anthropic-ai/claude-code | 2.1.63 |
| git-cliff | 2.12.0 |

## その他（開発関連）

| ツール | バージョン |
|---|---|
| Power Automate Process Mining | 6.1.2603.10218 |
| Google Antigravity | 1.20.5 |
| Microsoft Visual C++ 2015-2022 再頒布可能パッケージ | x64/x86 両方 |

---

## 初期化後の再インストール手順

```powershell
# 1. winget で一括インストール
winget install Git.Git
winget install OpenJS.NodeJS.LTS
winget install Microsoft.DotNet.SDK.8
winget install Microsoft.DotNet.SDK.10
winget install Microsoft.VisualStudioCode
winget install Docker.DockerDesktop
winget install GitHub.cli
winget install Ollama.Ollama
winget install Graphviz.Graphviz
winget install sakura-editor.sakura
winget install Amazon.Kiro

# 2. npm グローバルパッケージ
npm install -g @anthropic-ai/claude-code
npm install -g git-cliff
```

> **注意**: Pythonは元々インストールされていません。必要なら `winget install Python.Python.3.12` で追加。
