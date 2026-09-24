# 在项目根目录创建带图标的快捷方式「智能税务筹划.lnk」（Update 6.2）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1

$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root "启动税务分析.bat"
$icon = Join-Path $root "assets\app.ico"
$lnk = Join-Path $root "智能税务筹划.lnk"

if (-not (Test-Path $target)) {
  Write-Error "未找到 $target"
  exit 1
}

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $target
$sc.WorkingDirectory = $root
if (Test-Path $icon) { $sc.IconLocation = "$icon,0" }
$sc.Description = "智能税务筹划系统 · 一键启动"
$sc.Save()

Write-Output "已创建快捷方式：$lnk"
if (Test-Path $icon) { Write-Output "图标：$icon" } else { Write-Output "图标：未找到 app.ico（使用默认图标）" }
