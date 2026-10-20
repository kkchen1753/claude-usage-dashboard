#!/bin/bash
# 建立 LaunchAgent：登入後常駐背景，偵測到 Claude 啟動就顯示使用量浮動視窗
# 移除：./install_macos.sh --uninstall
set -e
LABEL="com.claude-usage-widget"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" = "--uninstall" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "已移除自動啟動。"
  exit 0
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3)"
"$PYTHON" -c "import tkinter" || { echo "此 Python 沒有 tkinter，請改用 python.org 的安裝檔或 brew install python-tk"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$DIR/claude_usage_widget.py</string>
    <string>--watch</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "安裝完成！登入後會在背景待命，開啟 Claude 時自動跳出浮動視窗。"
