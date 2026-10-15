<#
.SYNOPSIS
    一键构建脚本：准备虚拟环境 → 安装依赖 → 跑测试 → 打包 exe。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build.ps1
    powershell -ExecutionPolicy Bypass -File .\build.ps1 -SkipTests -Clean

.NOTES
    不再依赖 poetry；只要本机有 Python 3.9+ 即可，脚本会自动建立 .venv。
#>
[CmdletBinding()]
param(
    # 指定用于创建虚拟环境的 Python（默认自动探测 py -3 / python）
    [string]$Python = '',
    # 构建前清理 build/dist 目录
    [switch]$Clean,
    # 跳过测试
    [switch]$SkipTests,
    # 跳过依赖安装（虚拟环境已就绪时使用）
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Get-BasePython {
    param([string]$Explicit)
    if ($Explicit) { return @{ Exe = $Explicit; Args = @() } }
    if (Get-Command py -ErrorAction SilentlyContinue) { return @{ Exe = 'py'; Args = @('-3') } }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @{ Exe = 'python'; Args = @() } }
    throw '未找到 Python，请先安装 Python 3.9+ 或使用 -Python 指定路径。'
}

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $base = Get-BasePython -Explicit $Python
    Write-Host "[1/5] 创建虚拟环境 .venv ..." -ForegroundColor Cyan
    & $base.Exe @($base.Args) -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
}
else {
    Write-Host "[1/5] 复用已有虚拟环境 .venv" -ForegroundColor Cyan
}

if (-not $SkipInstall) {
    Write-Host "[2/5] 安装依赖 ..." -ForegroundColor Cyan
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --quiet -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
}
else {
    Write-Host "[2/5] 跳过依赖安装" -ForegroundColor DarkGray
}

if (-not $SkipTests) {
    Write-Host "[3/5] 运行测试 ..." -ForegroundColor Cyan
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw '测试未通过，已中止构建' }
}
else {
    Write-Host "[3/5] 跳过测试" -ForegroundColor DarkGray
}

Write-Host "[4/5] 清理旧产物 ..." -ForegroundColor Cyan
if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $root 'build') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $root 'dist') -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[5/5] PyInstaller 打包 ..." -ForegroundColor Cyan
& $venvPython -m PyInstaller --noconfirm --clean Windows-Ruijie.spec
if ($LASTEXITCODE -ne 0) { throw '打包失败' }

Write-Host ''
Write-Host '构建完成，产物如下：' -ForegroundColor Green
Get-ChildItem -LiteralPath (Join-Path $root 'dist') -Filter *.exe |
    Select-Object @{ Name = '文件'; Expression = { $_.Name } },
                  @{ Name = '大小(MB)'; Expression = { [math]::Round($_.Length / 1MB, 2) } } |
    Format-Table -AutoSize
