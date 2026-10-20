#!/usr/bin/env python3
"""Claude 使用量浮動視窗。

預設以精簡模式顯示「目前工作階段 / 本週」兩個百分比，點一下展開完整資訊
（進度條、重設時間），再點一下收合。

執行模式：
    python claude_usage_widget.py           直接顯示浮動視窗
    python claude_usage_widget.py --watch   常駐背景，偵測到 Claude 啟動就跳出、關閉就隱藏
    python claude_usage_widget.py --demo    使用假資料預覽外觀

只使用 Python 標準函式庫（tkinter、urllib），不需要另外安裝套件。
"""

import argparse
import datetime as dt
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = Path.home() / ".claude_usage_widget_state.json"

DEFAULT_CONFIG = {
    # 偵測這些程序名稱（不分大小寫）來判斷 Claude 是否已啟動
    "process_names": ["claude.exe", "claude"],
    "refresh_seconds": 60,
    "process_check_seconds": 3,
    # 選填：claude.ai 的 sessionKey cookie。沒有 Claude Code 登入資訊時才需要
    "session_key": "",
    "org_id": "",
}

OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_AI_BASE = "https://claude.ai/api"
USER_AGENT = "claude-usage-widget/1.0"

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# 顏色：仿照 claude.ai 設定頁
COLORS = {
    "bg": "#ffffff",
    "border": "#d9d9d9",
    "text": "#1f1f1f",
    "muted": "#6b6b6b",
    "session": "#2f6fd8",
    "session_track": "#cfe0f7",
    "weekly": "#f2a900",
    "weekly_track": "#f7e0ad",
    "danger": "#d93025",
    "danger_track": "#f6c9c5",
}

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


# ---------------------------------------------------------------- 設定 / 狀態

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {**default, **data} if isinstance(default, dict) else data
    except (OSError, ValueError):
        return dict(default) if isinstance(default, dict) else default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- 取得使用量

class UsageError(Exception):
    pass


def read_claude_code_token():
    """讀取 Claude Code 的 OAuth access token（與 /usage 指令相同來源）。"""
    raw = None
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    candidates = [Path(config_dir)] if config_dir else []
    candidates.append(Path.home() / ".claude")
    for d in candidates:
        p = d / ".credentials.json"
        if p.is_file():
            raw = p.read_text(encoding="utf-8")
            break
    if raw is None and IS_MAC:
        try:
            raw = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            raw = None
    if not raw:
        return None, None
    try:
        oauth = json.loads(raw).get("claudeAiOauth") or {}
    except ValueError:
        return None, None
    return oauth.get("accessToken"), oauth.get("expiresAt")


def http_get_json(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise UsageError("登入已過期，請開啟一次 Claude Code 重新登入") from e
        raise UsageError(f"伺服器回應錯誤 HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise UsageError("無法連線，請檢查網路") from e
    except ValueError as e:
        raise UsageError("回應格式無法解析") from e


def fetch_usage(config):
    token, expires_at = read_claude_code_token()
    if token:
        if expires_at and expires_at / 1000 < dt.datetime.now().timestamp() and not config["session_key"]:
            raise UsageError("登入已過期，請開啟一次 Claude Code 重新登入")
        try:
            return http_get_json(OAUTH_USAGE_URL, {
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            })
        except UsageError:
            if not config["session_key"]:
                raise

    if config["session_key"]:
        headers = {"Cookie": f"sessionKey={config['session_key']}"}
        org_id = config["org_id"]
        if not org_id:
            orgs = http_get_json(f"{CLAUDE_AI_BASE}/organizations", headers)
            if not orgs:
                raise UsageError("找不到 claude.ai 組織")
            org_id = orgs[0]["uuid"]
        return http_get_json(f"{CLAUDE_AI_BASE}/organizations/{org_id}/usage", headers)

    raise UsageError("找不到登入資訊：請先登入 Claude Code，或在 config.json 填入 session_key")


def demo_usage():
    now = dt.datetime.now(dt.timezone.utc)
    return {
        "five_hour": {"utilization": 2, "resets_at": (now + dt.timedelta(hours=3, minutes=12)).isoformat()},
        "seven_day": {"utilization": 75, "resets_at": (now + dt.timedelta(days=2, hours=5)).isoformat()},
    }


# ---------------------------------------------------------------- 格式化

def parse_time(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def format_clock(t):
    period = "上午" if t.hour < 12 else "下午"
    hour = t.hour % 12 or 12
    return f"{period}{hour}:{t.minute:02d}"


def format_reset(value):
    t = parse_time(value)
    if t is None:
        return ""
    now = dt.datetime.now().astimezone()
    if t.date() == now.date():
        return f"於 {format_clock(t)} 重設"
    if t.date() == (now + dt.timedelta(days=1)).date():
        return f"明天{format_clock(t)} 重設"
    return f"{WEEKDAYS[t.weekday()]}{format_clock(t)} 重設"


def utilization(entry):
    if not isinstance(entry, dict) or entry.get("utilization") is None:
        return None
    return max(0.0, min(100.0, float(entry["utilization"])))


def usage_rows(data):
    """把 API 回應轉成 (標題, 百分比, 重設文字, 顏色組) 清單。"""
    rows = [
        ("目前工作階段", data.get("five_hour"), "session"),
        ("本週", data.get("seven_day"), "weekly"),
        ("本週（Opus）", data.get("seven_day_opus"), "weekly"),
        ("本週（Sonnet）", data.get("seven_day_sonnet"), "weekly"),
    ]
    result = []
    for title, entry, palette in rows:
        pct = utilization(entry)
        if pct is None:
            continue
        result.append((title, pct, format_reset(entry.get("resets_at")), palette))
    return result


# ---------------------------------------------------------------- 程序偵測

def running_process_names():
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            return {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line}
        out = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=10).stdout
        return {os.path.basename(line.strip()).lower() for line in out.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        return set()


def claude_is_running(config):
    names = {n.lower() for n in config["process_names"]}
    return bool(names & running_process_names())


# ---------------------------------------------------------------- 介面

class UsageWidget:
    def __init__(self, root, config, demo=False, watch=False):
        self.root = root
        self.config = config
        self.demo = demo
        self.watch = watch
        self.state = load_json(STATE_PATH, {"right": None, "y": None})
        self.expanded = False
        self.data = None
        self.error = None
        self.updated_at = None
        self.results = queue.Queue()
        self.fetching = False
        self.hidden_until_restart = False
        self.claude_was_running = False

        family = self.pick_font()
        self.font = tkfont.Font(family=family, size=10)
        self.font_small = tkfont.Font(family=family, size=9)
        self.font_bold = tkfont.Font(family=family, size=11, weight="bold")
        self.font_title = tkfont.Font(family=family, size=10, weight="bold")

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=COLORS["border"])
        self.body = tk.Frame(root, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True, padx=1, pady=1)

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="立即重新整理", command=self.refresh)
        self.menu.add_command(label="展開 / 收合", command=self.toggle)
        self.menu.add_separator()
        if watch:
            self.menu.add_command(label="隱藏（下次啟動 Claude 再顯示）", command=self.hide_until_restart)
        self.menu.add_command(label="結束程式", command=self.quit)

        self.drag_start = None
        self.init_anchor()
        self.render()
        self.refresh()
        self.root.after(200, self.poll_results)
        self.root.after(self.config["refresh_seconds"] * 1000, self.auto_refresh)
        if watch:
            self.root.withdraw()
            self.check_process()

    # ---- 外觀
    def pick_font(self):
        available = set(tkfont.families(self.root))
        for name in ("Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC",
                     "Noto Sans CJK TC", "WenQuanYi Zen Hei", "Segoe UI"):
            if name in available:
                return name
        return tkfont.nametofont("TkDefaultFont").actual("family")

    def init_anchor(self):
        # 視窗以右上角為錨點：內容變寬（展開、資料載入）時往左長，不會跑出螢幕
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        right, y = self.state.get("right"), self.state.get("y")
        if right is None or y is None or not (40 < right <= sw and 0 <= y < sh - 20):
            right, y = sw - 24, 80
        self.anchor_right, self.anchor_top = right, y

    def place_window(self):
        self.root.update_idletasks()
        x = max(0, self.anchor_right - self.root.winfo_reqwidth())
        self.root.geometry(f"+{x}+{self.anchor_top}")

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def bind_interactions(self, widget):
        widget.bind("<ButtonPress-1>", self.on_press)
        widget.bind("<B1-Motion>", self.on_drag)
        widget.bind("<ButtonRelease-1>", self.on_release)
        widget.bind("<Button-3>", self.show_menu)
        if IS_MAC:
            widget.bind("<Button-2>", self.show_menu)
            widget.bind("<Control-Button-1>", self.show_menu)
        for child in widget.winfo_children():
            self.bind_interactions(child)

    def bar_color(self, pct, palette):
        if pct >= 90:
            return COLORS["danger"], COLORS["danger_track"]
        return COLORS[palette], COLORS[f"{palette}_track"]

    def render(self):
        self.clear()
        rows = usage_rows(self.data) if self.data else []
        if self.expanded:
            self.render_expanded(rows)
        else:
            self.render_compact(rows)
        self.bind_interactions(self.body)
        self.place_window()

    def render_compact(self, rows):
        frame = tk.Frame(self.body, bg=COLORS["bg"], padx=10, pady=5, cursor="hand2")
        frame.pack()
        if not rows:
            text = "⚠" if self.error else "…"
            tk.Label(frame, text=text, font=self.font_bold, bg=COLORS["bg"],
                     fg=COLORS["danger"] if self.error else COLORS["muted"]).pack()
            return
        for i, (_, pct, _, palette) in enumerate(rows[:2]):
            if i:
                tk.Label(frame, text="·", font=self.font_bold, bg=COLORS["bg"],
                         fg=COLORS["muted"]).pack(side="left", padx=4)
            tk.Label(frame, text=f"{pct:.0f}%", font=self.font_bold, bg=COLORS["bg"],
                     fg=self.bar_color(pct, palette)[0]).pack(side="left")
        if self.error:
            tk.Label(frame, text=" ⚠", font=self.font_small, bg=COLORS["bg"],
                     fg=COLORS["danger"]).pack(side="left")

    def render_expanded(self, rows):
        frame = tk.Frame(self.body, bg=COLORS["bg"], padx=16, pady=12)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg=COLORS["bg"])
        header.pack(fill="x", pady=(0, 6))
        tk.Label(header, text="Claude 使用量", font=self.font_title, bg=COLORS["bg"],
                 fg=COLORS["text"]).pack(side="left")
        tk.Label(header, text="點一下收合", font=self.font_small, bg=COLORS["bg"],
                 fg=COLORS["muted"]).pack(side="right")

        bar_width = 260
        grid = tk.Frame(frame, bg=COLORS["bg"])
        grid.pack(fill="x")
        for i, (title, pct, reset, palette) in enumerate(rows):
            r = i * 2
            pad_top = 0 if i == 0 else 10
            tk.Label(grid, text=title, font=self.font, bg=COLORS["bg"], fg=COLORS["text"],
                     anchor="w").grid(row=r, column=0, sticky="w", pady=(pad_top, 0))
            tk.Label(grid, text=reset, font=self.font_small, bg=COLORS["bg"], fg=COLORS["muted"],
                     anchor="w").grid(row=r + 1, column=0, sticky="w")
            fg, track = self.bar_color(pct, palette)
            canvas = tk.Canvas(grid, width=bar_width, height=8, bg=COLORS["bg"],
                               highlightthickness=0)
            canvas.grid(row=r, column=1, rowspan=2, padx=(20, 16), pady=(pad_top, 0))
            canvas.create_rectangle(0, 1, bar_width, 7, fill=track, outline="")
            filled = max(6, bar_width * pct / 100) if pct > 0 else 0
            if filled:
                canvas.create_rectangle(0, 1, filled, 7, fill=fg, outline="")
            tk.Label(grid, text=f"已使用 {pct:.0f}%", font=self.font, bg=COLORS["bg"],
                     fg=COLORS["muted"], anchor="e").grid(row=r, column=2, rowspan=2, sticky="e",
                                                          pady=(pad_top, 0))

        if not rows and not self.error:
            tk.Label(frame, text="讀取中…", font=self.font, bg=COLORS["bg"],
                     fg=COLORS["muted"]).pack(anchor="w", pady=6)

        footer_text = ""
        if self.updated_at:
            footer_text = f"更新於 {format_clock(self.updated_at)}"
        if self.error:
            tk.Label(frame, text=self.error, font=self.font_small, bg=COLORS["bg"],
                     fg=COLORS["danger"], anchor="w", justify="left",
                     wraplength=400).pack(fill="x", pady=(6, 0))
        if footer_text:
            tk.Label(frame, text=footer_text + "　·　右鍵開啟選單", font=self.font_small,
                     bg=COLORS["bg"], fg=COLORS["muted"], anchor="w").pack(fill="x", pady=(6, 0))

    # ---- 互動
    def on_press(self, event):
        self.drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())
        self.dragged = False

    def on_drag(self, event):
        if not self.drag_start:
            return
        sx, sy, wx, wy = self.drag_start
        dx, dy = event.x_root - sx, event.y_root - sy
        if abs(dx) > 3 or abs(dy) > 3:
            self.dragged = True
        if self.dragged:
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def on_release(self, _event):
        if self.drag_start and not self.dragged:
            self.toggle()
        elif self.dragged:
            self.anchor_right = self.root.winfo_x() + self.root.winfo_width()
            self.anchor_top = self.root.winfo_y()
            self.state.update(right=self.anchor_right, y=self.anchor_top)
            save_json(STATE_PATH, self.state)
        self.drag_start = None

    def show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def toggle(self):
        self.expanded = not self.expanded
        self.render()
        if self.expanded:
            self.refresh()

    def hide_until_restart(self):
        self.hidden_until_restart = True
        self.root.withdraw()

    def quit(self):
        self.root.destroy()

    # ---- 資料更新
    def refresh(self):
        if self.fetching:
            return
        self.fetching = True

        def worker():
            try:
                data = demo_usage() if self.demo else fetch_usage(self.config)
                self.results.put(("ok", data))
            except UsageError as e:
                self.results.put(("error", str(e)))
            except Exception as e:  # 背景執行緒不能讓例外悄悄消失
                self.results.put(("error", f"發生錯誤：{e}"))

        threading.Thread(target=worker, daemon=True).start()

    def poll_results(self):
        try:
            while True:
                kind, payload = self.results.get_nowait()
                self.fetching = False
                if kind == "ok":
                    self.data, self.error = payload, None
                    self.updated_at = dt.datetime.now()
                else:
                    self.error = payload
                self.render()
        except queue.Empty:
            pass
        self.root.after(200, self.poll_results)

    def auto_refresh(self):
        if self.root.state() != "withdrawn":
            self.refresh()
        self.root.after(self.config["refresh_seconds"] * 1000, self.auto_refresh)

    def check_process(self):
        running = claude_is_running(self.config)
        if running and not self.claude_was_running:
            self.hidden_until_restart = False
        if running and not self.hidden_until_restart:
            if self.root.state() == "withdrawn":
                self.root.deiconify()
                self.root.attributes("-topmost", True)
                self.refresh()
        elif self.root.state() != "withdrawn":
            self.root.withdraw()
        self.claude_was_running = running
        self.root.after(self.config["process_check_seconds"] * 1000, self.check_process)


def main():
    parser = argparse.ArgumentParser(description="Claude 使用量浮動視窗")
    parser.add_argument("--watch", action="store_true", help="常駐背景，Claude 啟動時自動顯示")
    parser.add_argument("--demo", action="store_true", help="使用假資料預覽")
    parser.add_argument("--expanded", action="store_true", help="啟動時直接展開")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    root = tk.Tk()
    root.title("Claude 使用量")
    widget = UsageWidget(root, config, demo=args.demo, watch=args.watch)
    if args.expanded:
        widget.toggle()
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
