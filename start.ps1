param([switch]$SkipInstall, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Assert-Exit($Label) {
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
# Load the same simple KEY=value environment file used by the backend.
Get-Content '.env' | ForEach-Object {
    if ($_ -match '^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$') {
        $value = $Matches[2].Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($Matches[1], $value, 'Process')
    }
}

if (-not $SkipInstall) {
    python -m uv --version *> $null
    if ($LASTEXITCODE -ne 0) {
        python -m pip install uv
        Assert-Exit 'Installing uv'
    }
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        python -m uv venv --python 3.13 .venv
        Assert-Exit 'Creating Python environment'
    }
    python -m uv pip sync --python .venv\Scripts\python.exe backend\requirements.txt
    Assert-Exit 'Installing backend dependencies'
    Push-Location frontend
    try { npm.cmd ci; Assert-Exit 'Installing frontend dependencies' } finally { Pop-Location }
}

function Start-ServiceWindow($Title, $Directory, $Command) {
    $escaped = $Directory.Replace("'", "''")
    Start-Process powershell.exe -ArgumentList '-NoExit', '-Command', "`$Host.UI.RawUI.WindowTitle='$Title'; Set-Location '$escaped'; $Command"
}

$headers = @{}
if ($env:OPENCODE_SERVER_TOKEN) { $headers.Authorization = "Bearer $env:OPENCODE_SERVER_TOKEN" }
elseif ($env:OPENCODE_SERVER_PASSWORD) {
    $username = if ($env:OPENCODE_SERVER_USERNAME) { $env:OPENCODE_SERVER_USERNAME } else { 'opencode' }
    $basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${username}:$env:OPENCODE_SERVER_PASSWORD"))
    $headers.Authorization = "Basic $basic"
}
$engine = $env:OPENCODE_SERVER_URL.TrimEnd('/')
try { $null = Invoke-RestMethod "$engine/api/info" -Headers $headers -TimeoutSec 5 }
catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) { throw 'OpenCode authentication failed. Check .env credentials.' }
    $uri = [Uri]$engine
    if ($uri.Host -notin @('localhost', '127.0.0.1')) { throw 'Remote OpenCode engine is unavailable.' }
    if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) { throw 'Install OpenCode V2 and configure a model first.' }
    if (-not $env:OPENCODE_SERVER_PASSWORD -and -not $env:OPENCODE_SERVER_TOKEN) {
        $env:OPENCODE_SERVER_PASSWORD = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
        Add-Content '.env' "`nOPENCODE_SERVER_PASSWORD=$env:OPENCODE_SERVER_PASSWORD"
    }
    $env:OPENCODE_PASSWORD = $env:OPENCODE_SERVER_PASSWORD
    Start-ServiceWindow 'Orbit - OpenCode' $PSScriptRoot "opencode serve --hostname 127.0.0.1 --port $($uri.Port)"
}
try { $null = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 }
catch { Start-ServiceWindow 'Orbit - Backend' $PSScriptRoot '& .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000' }
try { $null = Invoke-WebRequest 'http://127.0.0.1:3000' -UseBasicParsing -TimeoutSec 2 }
catch { Start-ServiceWindow 'Orbit - Frontend' (Join-Path $PSScriptRoot 'frontend') 'npm.cmd run dev' }
Write-Host 'Mission Control: http://127.0.0.1:3000' -ForegroundColor Green
Write-Host 'Close the service windows to stop. OpenCode sessions are retained.'
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:3000' }
