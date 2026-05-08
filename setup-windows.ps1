<#
.SYNOPSIS
    Bootstrap the ExtractData project on a fresh Windows machine.

.DESCRIPTION
    Walks through everything end-to-end:

      1. Verifies winget is available.
      2. Installs Git, Python 3.12, Tesseract OCR, and Ollama via winget
         (skips anything already installed).
      3. Refreshes PATH so newly-installed tools are usable in this session.
      4. Confirms tesseract has Hebrew language data; offers to download it
         if not (from tesseract-ocr/tessdata_best).
      5. Confirms Ollama is running; tries to start it if not.
      6. Pulls the gemma4:latest model (~9.6 GB) if missing.
      7. Creates a Python venv at .venv and installs requirements.txt
         (and requirements-dev.txt on request).
      8. Runs an end-to-end smoke test against the case_01 fixture.

    Idempotent: re-running skips work that's already done.
    Interactive: prompts before each install. Use -NonInteractive to skip
    prompts and accept all defaults.

.PARAMETER NonInteractive
    Don't prompt; accept defaults. Good for unattended setup.

.PARAMETER SkipModelPull
    Skip the gemma4:latest download.

.PARAMETER SkipSmokeTest
    Skip the end-to-end smoke test at the end.

.EXAMPLE
    .\setup-windows.ps1

.EXAMPLE
    .\setup-windows.ps1 -NonInteractive

.NOTES
    Run from the project root in a non-admin PowerShell. winget will
    request elevation per package via UAC where required.

    If PowerShell refuses to run the script, allow it once with:
        powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
    or grant your user execution permission permanently:
        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$SkipModelPull,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = 'Stop'

# ---- output helpers --------------------------------------------------------
function Write-Step ($m) { Write-Host "";          Write-Host "==> $m"  -ForegroundColor Cyan }
function Write-Ok   ($m) { Write-Host "    [OK]  $m" -ForegroundColor Green }
function Write-Warn ($m) { Write-Host "    [WARN] $m" -ForegroundColor Yellow }
function Write-Fail ($m) { Write-Host "    [FAIL] $m" -ForegroundColor Red }
function Write-Info ($m) { Write-Host "    $m" -ForegroundColor Gray }

function Confirm-Continue($prompt) {
    if ($NonInteractive) { Write-Info "(auto-Y) $prompt"; return $true }
    $resp = Read-Host "    $prompt [Y/n]"
    return ($resp -eq '' -or $resp -match '^[Yy]')
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Test-Cmd($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Install-WingetPkg {
    param(
        [Parameter(Mandatory)] [string]$Id,
        [Parameter(Mandatory)] [string]$DisplayName,
        [string]$ProbeCmd
    )
    Write-Step "Checking $DisplayName ($Id)"
    if ($ProbeCmd -and (Test-Cmd $ProbeCmd)) {
        Write-Ok  "$DisplayName already on PATH (skipping winget)"
        return
    }
    $listed = winget list --id $Id --exact 2>$null | Out-String
    if ($listed -match [regex]::Escape($Id)) {
        Write-Ok  "$DisplayName already installed (per winget)"
        return
    }
    if (-not (Confirm-Continue "Install $DisplayName via winget?")) {
        Write-Warn "Skipped $DisplayName"
        return
    }
    Write-Info "Running: winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements"
    winget install --id $Id --exact --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "winget install $Id failed (exit code $LASTEXITCODE)"
        throw "Aborted: $Id install failed"
    }
    Write-Ok "$DisplayName installed"
    Refresh-Path
}

# ---- banner ----------------------------------------------------------------
Write-Host ""
Write-Host "========================================================" -ForegroundColor Magenta
Write-Host "  ExtractData — Windows bootstrap" -ForegroundColor Magenta
Write-Host "========================================================" -ForegroundColor Magenta

# ---- 0) winget --------------------------------------------------------------
Write-Step "Verifying winget is available"
if (-not (Test-Cmd 'winget')) {
    Write-Fail "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
    exit 1
}
Write-Ok "winget present"

# ---- 1-4) tools via winget --------------------------------------------------
Install-WingetPkg -Id 'Git.Git'                  -DisplayName 'Git for Windows' -ProbeCmd 'git'
Install-WingetPkg -Id 'Python.Python.3.12'       -DisplayName 'Python 3.12'     -ProbeCmd 'python'
Install-WingetPkg -Id 'UB-Mannheim.TesseractOCR' -DisplayName 'Tesseract OCR'   -ProbeCmd 'tesseract'
Install-WingetPkg -Id 'Ollama.Ollama'            -DisplayName 'Ollama'          -ProbeCmd 'ollama'

Refresh-Path

# ---- 5) version sanity check -----------------------------------------------
Write-Step "Verifying tool versions"
$missing = @()
foreach ($t in @('git','python','tesseract','ollama')) {
    if (Test-Cmd $t) {
        $ver = (& $t --version 2>&1 | Select-Object -First 1).ToString().Trim()
        Write-Ok "$t : $ver"
    } else {
        $missing += $t
        Write-Fail "$t not on PATH"
    }
}
if ($missing.Count -gt 0) {
    Write-Warn "Some tools aren't on PATH yet. Close this terminal, open a new PowerShell, and re-run setup-windows.ps1."
    exit 1
}

# ---- 6) Tesseract Hebrew language data --------------------------------------
Write-Step "Checking Tesseract Hebrew language data"
$tessBin    = (Get-Command tesseract).Source
$tessRoot   = Split-Path $tessBin -Parent
$tessdata   = Join-Path $tessRoot 'tessdata'
$hebFile    = Join-Path $tessdata 'heb.traineddata'
if (Test-Path $hebFile) {
    Write-Ok "Hebrew traineddata present at $hebFile"
} else {
    Write-Warn "heb.traineddata missing in $tessdata"
    if (Confirm-Continue "Download heb.traineddata from tesseract-ocr/tessdata_best (~13 MB)?") {
        $url = 'https://github.com/tesseract-ocr/tessdata_best/raw/main/heb.traineddata'
        try {
            Invoke-WebRequest -Uri $url -OutFile $hebFile -UseBasicParsing
            Write-Ok "Downloaded $hebFile"
        } catch {
            Write-Fail "Download failed: $_"
            Write-Info "Workaround: re-run the Tesseract installer GUI and tick 'Hebrew' under Additional language data."
            exit 1
        }
    } else {
        Write-Fail "Cannot proceed without Hebrew language data."
        exit 1
    }
}

# Verify tesseract sees both languages
$langs = (& tesseract --list-langs 2>&1 | Out-String)
if ($langs -match '\bheb\b') { Write-Ok "tesseract --list-langs includes 'heb'" }
else { Write-Fail "tesseract still doesn't see 'heb'. Check $tessdata."; exit 1 }
if ($langs -match '\beng\b') { Write-Ok "tesseract --list-langs includes 'eng'" }
else { Write-Warn "tesseract --list-langs does not include 'eng' — most installs ship it by default." }

# ---- 7) Ollama service ------------------------------------------------------
function Test-OllamaUp {
    try {
        Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' `
            -UseBasicParsing -TimeoutSec 5 | Out-Null
        return $true
    } catch { return $false }
}

Write-Step "Checking Ollama service"
if (Test-OllamaUp) {
    Write-Ok "Ollama responding on http://localhost:11434"
} else {
    Write-Warn "Ollama not running. Trying to start..."
    Start-Process -FilePath ollama -ArgumentList 'serve' -WindowStyle Hidden
    Start-Sleep -Seconds 5
    if (Test-OllamaUp) {
        Write-Ok "Ollama now responding"
    } else {
        Write-Fail "Ollama still not responding. Open the Ollama tray app and re-run this script."
        exit 1
    }
}

# ---- 8) gemma4:latest model -------------------------------------------------
if (-not $SkipModelPull) {
    Write-Step "Checking gemma4:latest model"
    $tags = (Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing).Content `
            | ConvertFrom-Json
    $hasModel = $tags.models | Where-Object { $_.name -eq 'gemma4:latest' }
    if ($hasModel) {
        Write-Ok "gemma4:latest already pulled"
    } else {
        if (Confirm-Continue "Pull gemma4:latest (~9.6 GB)?") {
            ollama pull gemma4:latest
            if ($LASTEXITCODE -ne 0) {
                Write-Fail "ollama pull failed (exit code $LASTEXITCODE)"
                exit 1
            }
            Write-Ok "gemma4:latest pulled"
        } else {
            Write-Warn "Pipeline will fail at runtime without gemma4:latest."
        }
    }
}

# ---- 9) Python venv + deps --------------------------------------------------
$projectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Write-Step "Setting up Python venv at $projectRoot\.venv"

$venvDir    = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (Test-Path $venvPython) {
    Write-Ok ".venv already exists"
} else {
    python -m venv $venvDir
    if (-not (Test-Path $venvPython)) {
        Write-Fail "venv creation failed"
        exit 1
    }
    Write-Ok ".venv created"
}

Write-Step "Installing Python dependencies"
& $venvPython -m pip install --upgrade pip --quiet
Write-Ok "pip upgraded"

$reqRuntime = Join-Path $projectRoot 'requirements.txt'
if (Test-Path $reqRuntime) {
    & $venvPython -m pip install -r $reqRuntime
    if ($LASTEXITCODE -ne 0) { Write-Fail "pip install runtime failed"; exit 1 }
    Write-Ok "runtime requirements installed"
} else {
    Write-Fail "requirements.txt not found at $reqRuntime"
    exit 1
}

$reqDev = Join-Path $projectRoot 'requirements-dev.txt'
if (Test-Path $reqDev) {
    if (Confirm-Continue "Install dev requirements too (pytest, python-bidi)?") {
        & $venvPython -m pip install -r $reqDev
        if ($LASTEXITCODE -ne 0) { Write-Fail "pip install dev failed"; exit 1 }
        Write-Ok "dev requirements installed"
    }
}

# ---- 10) smoke test ---------------------------------------------------------
if (-not $SkipSmokeTest) {
    Write-Step "Running end-to-end smoke test"
    if (Confirm-Continue "Run smoke test on tests\fixtures\case_01_pigeons_yes?") {
        Push-Location $projectRoot
        try {
            $genFixtures = Join-Path $projectRoot 'tests\generate_fixtures.py'
            $extractPy   = Join-Path $projectRoot 'extract.py'
            $schema      = Join-Path $projectRoot 'schema.yaml'
            $outDir      = Join-Path $projectRoot 'out'
            $smokeXlsx   = Join-Path $outDir 'smoke.xlsx'
            $caseDir     = Join-Path $projectRoot 'tests\fixtures\case_01_pigeons_yes'
            if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
            if (-not (Test-Path $caseDir)) {
                Write-Info "Generating test fixtures first..."
                & $venvPython $genFixtures --case case_01_pigeons_yes
            }
            Write-Info "Extracting case_01 -> $smokeXlsx (this calls Tesseract + gemma4:latest)..."
            & $venvPython $extractPy $caseDir $schema $smokeXlsx
            if ($LASTEXITCODE -ne 0) {
                Write-Fail "Smoke test extraction failed (exit code $LASTEXITCODE)"
                exit 1
            }
            if (Test-Path $smokeXlsx) {
                Write-Ok "Smoke test wrote $smokeXlsx"
            } else {
                Write-Fail "Smoke test ran but no xlsx was produced."
                exit 1
            }
        } finally {
            Pop-Location
        }
    }
}

# ---- done -------------------------------------------------------------------
Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  All set." -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Activate the venv:"
Write-Host "      .venv\Scripts\Activate.ps1"
Write-Host ""
Write-Host "  Run extraction on a real folder:"
Write-Host "      python extract.py `"C:\path\to\folder-of-screenshots`" schema.yaml out\result.xlsx"
Write-Host ""
Write-Host "  Run the test suite:"
Write-Host "      python -m pytest tests\test_pipeline.py -v"
Write-Host ""
