param([switch]$PipelineOnly, [switch]$SkipInstall, [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location (Split-Path -Parent $PSScriptRoot)
$projectRoot = (Get-Location).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if ($pythonCommand) { $bootstrapPython = $pythonCommand.Source }
    elseif (Test-Path -LiteralPath $bundledPython) { $bootstrapPython = $bundledPython }
    else { throw 'Install Python 3.12+ and add python to PATH.' }
    & $bootstrapPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python virtual environment.' }
}
$requirementsPath = Join-Path $projectRoot 'backend\requirements.txt'
$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
$markerPath = Join-Path $projectRoot '.venv\requirements.sha256'
if (-not $SkipInstall -and ((-not (Test-Path -LiteralPath $markerPath)) -or (Get-Content -LiteralPath $markerPath -Raw).Trim() -ne $requirementsHash)) {
    & $pythonPath -m pip install -r $requirementsPath
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    Set-Content -LiteralPath $markerPath -Value $requirementsHash
}
if ($PipelineOnly) {
    & $pythonPath -m backend.app.analytics
    if ($LASTEXITCODE -ne 0) { throw 'Analytics pipeline failed.' }
    exit 0
}
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
$pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
$bundledPnpm = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd'
if ($npmCommand) { $packageManager = $npmCommand.Source; $managerName = 'npm' }
elseif ($pnpmCommand) { $packageManager = $pnpmCommand.Source; $managerName = 'pnpm' }
elseif (Test-Path -LiteralPath $bundledPnpm) { $packageManager = $bundledPnpm; $managerName = 'pnpm' }
else { throw 'Install Node.js 22 LTS or newer (with npm).' }
Push-Location frontend
try {
    if (-not $SkipInstall) {
        if ($managerName -eq 'npm') { & $packageManager ci }
        else { & $packageManager install --frozen-lockfile }
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    & $packageManager run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host "Graph Money: http://127.0.0.1:$Port | API docs: http://127.0.0.1:$Port/docs"
& $pythonPath -m uvicorn backend.app.main:app --host 127.0.0.1 --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Application stopped with an error.' }
