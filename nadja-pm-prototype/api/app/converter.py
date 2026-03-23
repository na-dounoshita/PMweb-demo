"""UIアクティビティモニター(UIAM)ログ → プロセスマイニングCSV 変換モジュール."""

from __future__ import annotations

import re

import pandas as pd

UIAM_REQUIRED_COLUMNS = {"Timestamp", "EventType", "ProcessName", "WindowTitle"}

# ProcessName → 人間可読 Activity 名（非ブラウザ用）
DEFAULT_ACTIVITY_MAP: dict[str, str] = {
    "explorer": "Explorer",
    "OUTLOOK": "Outlook",
    "EXCEL": "Excel",
    "WINWORD": "Word",
    "POWERPNT": "PowerPoint",
    "Teams": "Teams",
    "Slack": "Slack",
    "Code": "VS Code",
    "Docker Desktop": "Docker Desktop",
    "WindowsTerminal": "Terminal",
    "cmd": "Command Prompt",
    "powershell": "PowerShell",
    "notepad": "Notepad",
}

BROWSER_PROCESSES = {"chrome", "msedge", "firefox", "opera", "brave"}

# ブラウザの WindowTitle 末尾パターン（例: "- Google Chrome", "- Microsoft Edge"）
_BROWSER_SUFFIX_RE = re.compile(
    r"\s*-\s*(?:Google Chrome|Microsoft Edge|Mozilla Firefox|Opera|Brave)\s*$"
)

# WindowTitle からWebアプリを識別するキーワード → Activity名
# タイトルに含まれるキーワードで判定（大文字小文字無視）
WEBAPP_KEYWORDS: dict[str, str] = {
    "slack": "Slack",
    "notion": "Notion",
    "claude": "Claude",
    "chatgpt": "ChatGPT",
    "gmail": "Gmail",
    "google sheets": "Google Sheets",
    "google docs": "Google Docs",
    "google drive": "Google Drive",
    "google calendar": "Google Calendar",
    "google meet": "Google Meet",
    "outlook": "Outlook Web",
    "microsoft teams": "Teams Web",
    "github": "GitHub",
    "gitlab": "GitLab",
    "jira": "Jira",
    "confluence": "Confluence",
    "salesforce": "Salesforce",
    "figma": "Figma",
    "miro": "Miro",
    "trello": "Trello",
    "asana": "Asana",
    "linear": "Linear",
    "youtube": "YouTube",
    "twitter": "Twitter",
    "facebook": "Facebook",
}


def detect_uiam_format(df: pd.DataFrame) -> bool:
    """DataFrame が UIAM ログ形式かどうかを判定する。"""
    return UIAM_REQUIRED_COLUMNS.issubset(set(df.columns))


def _detect_webapp_from_title(window_title: str) -> str | None:
    """WindowTitle からWebアプリ名を識別する。見つからなければ None。"""
    if not window_title or pd.isna(window_title):
        return None
    title_lower = str(window_title).lower()
    for keyword, app_name in WEBAPP_KEYWORDS.items():
        if keyword in title_lower:
            return app_name
    return None


def _resolve_activity_key(process_name: str, window_title: str) -> str:
    """ProcessName と WindowTitle からアクティビティ識別キーを決定する。

    ブラウザの場合は WindowTitle からWebアプリを検出してキーにする。
    非ブラウザの場合は ProcessName をそのままキーにする。
    """
    pn = str(process_name).strip() if process_name and not pd.isna(process_name) else ""
    if pn.lower() in BROWSER_PROCESSES:
        webapp = _detect_webapp_from_title(window_title)
        if webapp:
            return f"browser:{webapp}"
        return f"browser:{pn}"
    return pn


def _resolve_activity(activity_key: str, activity_map: dict[str, str] | None) -> str:
    """アクティビティキーを人間可読名に変換する。"""
    if not activity_key:
        return "Unknown"
    # ユーザー定義マップを最優先
    if activity_map and activity_key in activity_map:
        return activity_map[activity_key]
    # browser:XXX 形式の処理
    if activity_key.startswith("browser:"):
        app_name = activity_key[len("browser:"):]
        if activity_map and app_name in activity_map:
            return activity_map[app_name]
        # Webアプリ名ならそのまま返す（例: "Slack", "Notion"）
        # ProcessName（例: "chrome"）の場合はマップで変換
        if app_name.lower() in BROWSER_PROCESSES:
            return "Chrome" if app_name.lower() == "chrome" else app_name.title()
        return app_name
    # 非ブラウザ: デフォルトマップ
    if activity_map and activity_key in activity_map:
        return activity_map[activity_key]
    if activity_key in DEFAULT_ACTIVITY_MAP:
        return DEFAULT_ACTIVITY_MAP[activity_key]
    # 大文字小文字無視で検索
    key_lower = activity_key.lower()
    for k, v in DEFAULT_ACTIVITY_MAP.items():
        if k.lower() == key_lower:
            return v
    return activity_key


def _resolve_source(process_name: str) -> str:
    """ProcessName からソースカテゴリを判定する。"""
    if not process_name or pd.isna(process_name):
        return "Window"
    return "Browser" if str(process_name).strip().lower() in BROWSER_PROCESSES else "Window"


def convert_uiam_log(
    df: pd.DataFrame,
    activity_map: dict[str, str] | None = None,
    min_duration: int = 0,
) -> pd.DataFrame:
    """UIAM 生ログ DataFrame をプロセスマイニング用フォーマットに変換する。

    連続する同一 ProcessName のイベント群を1つのアクティビティセッションに集約し、
    Activity, Timestamp, Duration, Source の DataFrame を返す。
    """
    if not detect_uiam_format(df):
        raise ValueError(
            f"UIAMログ形式ではありません。必須カラム: {UIAM_REQUIRED_COLUMNS}"
        )

    df = df.copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    df = df.sort_values("Timestamp").reset_index(drop=True)

    # ProcessName が空の行は直前の値で埋める
    df["ProcessName"] = df["ProcessName"].ffill()
    # それでも空なら Unknown
    df["ProcessName"] = df["ProcessName"].fillna("Unknown")
    df["WindowTitle"] = df["WindowTitle"].fillna("")

    # アクティビティキーを算出（ブラウザの場合 WindowTitle からWebアプリを識別）
    df["_activity_key"] = df.apply(
        lambda r: _resolve_activity_key(r["ProcessName"], r["WindowTitle"]), axis=1
    )

    # 連続する同一アクティビティキーをグループ化
    df["_group"] = (df["_activity_key"] != df["_activity_key"].shift()).cumsum()

    sessions = []
    for _, group in df.groupby("_group", sort=True):
        process_name = group["ProcessName"].iloc[0]
        activity_key = group["_activity_key"].iloc[0]
        start_ts = group["Timestamp"].iloc[0]
        sessions.append(
            {
                "ActivityKey": activity_key,
                "Activity": _resolve_activity(activity_key, activity_map),
                "Timestamp": start_ts,
                "Source": _resolve_source(process_name),
            }
        )

    if not sessions:
        return pd.DataFrame(columns=["Activity", "Timestamp", "Duration", "Source"])

    result = pd.DataFrame(sessions)

    # Duration = 次セッション開始までの秒数（最後は0）
    result["Duration"] = 0
    for i in range(len(result) - 1):
        delta = (result.loc[i + 1, "Timestamp"] - result.loc[i, "Timestamp"]).total_seconds()
        result.loc[i, "Duration"] = int(delta)

    # min_duration フィルタ
    if min_duration > 0:
        result = result[result["Duration"] >= min_duration].reset_index(drop=True)

    # Timestamp を文字列に整形
    result["Timestamp"] = result["Timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    return result[["Activity", "Timestamp", "Duration", "Source"]]


def get_unique_activity_keys(df: pd.DataFrame) -> list[dict[str, str]]:
    """UIAM ログから一意のアクティビティキーとデフォルト Activity 名のペアを返す。

    ブラウザ内Webアプリも個別に列挙される。
    """
    if not detect_uiam_format(df):
        return []
    df = df.copy()
    df["ProcessName"] = df["ProcessName"].ffill().fillna("Unknown")
    df["WindowTitle"] = df["WindowTitle"].fillna("")
    keys = df.apply(
        lambda r: _resolve_activity_key(r["ProcessName"], r["WindowTitle"]), axis=1
    ).unique()
    return [
        {"ActivityKey": k, "Activity": _resolve_activity(k, None)}
        for k in sorted(keys)
    ]
