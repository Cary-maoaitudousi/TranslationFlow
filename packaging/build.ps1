# 打包成独立目录（含 exe），产物在 dist\TranslationFlow\
# 用法： .\packaging\build.ps1            （默认用 .venv311）
#        .\packaging\build.ps1 -Python .venv\Scripts\python.exe

param(
    [string]$Python = ".\.venv311\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/5] 检查 Python: $Python"
& $Python -V

Write-Host "[2/5] 安装/确认 PyInstaller"
& $Python -m pip install -q --disable-pip-version-check "pyinstaller>=6.0"

Write-Host "[3/5] 开始打包（首次约 1~3 分钟）"
& $Python -m PyInstaller --noconfirm --clean "packaging\TranslationFlow.spec"

$dist = Join-Path $root "dist\TranslationFlow"
if (-not (Test-Path $dist)) {
    throw "打包失败：找不到 $dist"
}

Write-Host "[4/5] 把配置文件与资源摆到 exe 同级（用户可改）"
$internal = Join-Path $dist "_internal"

Copy-Item (Join-Path $internal "config.example.json") (Join-Path $dist "config.example.json") -Force
Copy-Item (Join-Path $internal ".env.example") (Join-Path $dist ".env.example") -Force
Copy-Item (Join-Path $internal "README.md") (Join-Path $dist "README.md") -Force
if (-not (Test-Path (Join-Path $dist "config.json"))) {
    Copy-Item (Join-Path $dist "config.example.json") (Join-Path $dist "config.json")
}
New-Item -ItemType Directory -Force (Join-Path $dist "data") | Out-Null
Copy-Item (Join-Path $internal "data\noun_table.json") (Join-Path $dist "data\noun_table.json") -Force
if (-not (Test-Path (Join-Path $dist "samples"))) {
    Copy-Item (Join-Path $internal "samples") (Join-Path $dist "samples") -Recurse -Force
}
Copy-Item (Join-Path $internal "profiles") (Join-Path $dist "profiles") -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $dist "output") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $dist "logs") | Out-Null

Write-Host "[5/5] 自检（不联网）"
$exe = Join-Path $dist "TranslationFlow.exe"
& $exe --selftest | Out-Null
$report = Join-Path $dist "logs\selftest.txt"
if (Test-Path $report) { Get-Content $report } else { Write-Warning "没有生成 selftest.txt" }

$size = [math]::Round(((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "完成：$dist"
Write-Host "体积：$size MB"
Write-Host "双击 TranslationFlow.exe 启动；config.json / data / logs / output 都在同目录"
