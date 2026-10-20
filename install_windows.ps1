# 在「啟動」資料夾建立捷徑：開機後常駐背景，偵測到 Claude 啟動就顯示使用量浮動視窗
# 移除：powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Uninstall
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Startup")) "Claude Usage Widget.lnk"

if ($Uninstall) {
    if (Test-Path $shortcutPath) { Remove-Item $shortcutPath }
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*claude_usage_widget.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "已移除自動啟動。"
    exit 0
}

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $pyLauncher = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pyLauncher) { throw "找不到 pythonw.exe，請先從 https://www.python.org 安裝 Python 3（勾選 Add to PATH）。" }
    $pythonw = $pyLauncher
}

$script = Join-Path $PSScriptRoot "claude_usage_widget.py"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$script`" --watch"
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Save()

Start-Process -FilePath $pythonw -ArgumentList "`"$script`" --watch" -WorkingDirectory $PSScriptRoot
Write-Host "安裝完成！之後每次開機都會在背景待命，開啟 Claude 時自動跳出浮動視窗。"
