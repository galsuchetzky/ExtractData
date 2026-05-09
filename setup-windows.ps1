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
    [switch]$SkipSmokeTest,
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'

# Path to the current script directory
$projectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

# Fix OLLAMA_HOST if it's set to a bind-address like 0.0.0.0 (client needs a scheme)
if ($env:OLLAMA_HOST -eq '0.0.0.0' -or $env:OLLAMA_HOST -eq '127.0.0.1') {
    $env:OLLAMA_HOST = 'http://localhost:11434'
}

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

function Test-IsAdmin {
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Refresh-Path {
    Write-Info "Refreshing PATH from registry..."
    $machinePath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath    = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    
    # Start with registry paths to ensure we pick up new installs
    $allPaths = New-Object System.Collections.Generic.List[string]
    foreach ($p in ($machinePath + ";" + $userPath).Split(';', [System.StringSplitOptions]::RemoveEmptyEntries)) {
        $p = $p.Trim()
        if ($p -and -not $allPaths.Contains($p)) { [void]$allPaths.Add($p) }
    }

    # Also check registry for known tools that might not be on PATH
    $tessReg = Get-ItemProperty "HKLM:\SOFTWARE\Tesseract-OCR" -ErrorAction SilentlyContinue
    if ($tessReg -and (Test-Path $tessReg.InstallDir)) {
        $p = $tessReg.InstallDir.Trim()
        if ($p -and -not $allPaths.Contains($p)) { 
            [void]$allPaths.Add($p) 
            Write-Info "Found Tesseract in registry at $p"
        }
    }

    $env:Path = [string]::Join(';', $allPaths)
}

function Test-Cmd($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Install-WingetPkg {
    param(
        [Parameter(Mandatory)] [string]$Id,
        [Parameter(Mandatory)] [string]$DisplayName,
        [scriptblock]$Probe
    )
    Write-Step "Checking $DisplayName ($Id)"
    if ($Probe -and (& $Probe)) {
        Write-Ok  "$DisplayName already on PATH"
        return
    }
    
    $listed = winget list --id $Id --exact 2>$null | Out-String
    $isListed = $listed -match [regex]::Escape($Id)
    
    if ($isListed) {
        Write-Info "$DisplayName is registered with winget, but probe failed."
        Write-Info "Refreshing PATH to see if it appears..."
        Refresh-Path
        if ($Probe -and (& $Probe)) {
            Write-Ok "$DisplayName now found on PATH"
            return
        }
        Write-Warn "$DisplayName still not found. Re-running install may fix PATH/registry."
    }

    if (-not (Confirm-Continue "Install $DisplayName via winget?")) {
        Write-Warn "Skipped $DisplayName"
        return
    }
    Write-Info "Running: winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements --no-upgrade"
    # Some winget installs return 0x8A150039 (already installed but needs update) or similar.
    # 0x8A150030 and 0x8A150031 also mean already installed/cancelled.
    winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements --no-upgrade
    $exitCode = $LASTEXITCODE
    
    # If winget returns a non-zero code (even "already installed"), 
    # but our probe STILL fails, we must --force it.
    if ($exitCode -ne 0) {
        Refresh-Path
        if (-not (& $Probe)) {
            Write-Warn "$DisplayName probe failed (exit code $exitCode). Attempting --force reinstall..."
            winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements --force
            $exitCode = $LASTEXITCODE
        } else {
            # If the probe now succeeds, we treat it as a success
            $exitCode = 0
        }
    }

    # Final check: is it working now?
    if ($exitCode -ne 0 -and -not (& $Probe)) {
        Write-Fail "winget install $Id failed (exit code $exitCode)"
        Write-Info "Try running manually: winget install --id $Id --force"
        throw "Aborted: $Id install failed"
    }
    Write-Ok "$DisplayName installed/verified"
    Refresh-Path
}

# ---- banner ----------------------------------------------------------------
Write-Host ""
Write-Host "========================================================" -ForegroundColor Magenta
Write-Host "  ExtractData - Windows bootstrap" -ForegroundColor Magenta
Write-Host "========================================================" -ForegroundColor Magenta

# ---- 0) winget --------------------------------------------------------------
Write-Step "Verifying winget is available"
if (-not (Test-Cmd 'winget')) {
    Write-Fail "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
    exit 1
}
Write-Ok "winget present"

if (-not $SkipDeps) {
    # ---- 2) Git -----------------------------------------------------------------
    Install-WingetPkg -Id 'Git.Git' -DisplayName 'Git' -Probe { Test-Cmd 'git' }

    # Python 3.12 specifically
    Install-WingetPkg -Id 'Python.Python.3.12' -DisplayName 'Python 3.12' -Probe {
        if (Test-Cmd 'py') {
            # py -3.12 --version writes to stderr if 3.12 is missing, 
            # which can trigger NativeCommandError if redirected poorly.
            try {
                $null = & py -0 2>$null # list installed versions silently
                $list = & py -0 2>&1
                if ($list -match '3\.12') { return $true }
            } catch {}
        }
        if (Test-Cmd 'python') {
            try {
                $v = (& python --version 2>&1 | Out-String)
                return ($v -match '3\.12')
            } catch { return $false }
        }
        return $false
    }

    Install-WingetPkg -Id 'UB-Mannheim.TesseractOCR' -DisplayName 'Tesseract OCR' -Probe { Test-Cmd 'tesseract' }
    Install-WingetPkg -Id 'Ollama.Ollama' -DisplayName 'Ollama' -Probe { Test-Cmd 'ollama' }
}

Refresh-Path

# Find the best python
$PYTHON_EXE = "python"
if (Test-Cmd "py") {
    # If the Python Launcher (py) is available, use it to find/run 3.12
    $PYTHON_EXE = "py -3.12"
}
# Fallback: if 'python' is not 3.12, but we just installed it, Refresh-Path might help.
# We'll re-verify below.

function Run-Python {
    param([string[]]$ArgsList)
    if ($PYTHON_EXE -match ' ') {
        $parts = $PYTHON_EXE.Split(' ')
        & $parts[0] $parts[1] $ArgsList
    } else {
        & $PYTHON_EXE $ArgsList
    }
}

# ---- 5) version sanity check -----------------------------------------------
Write-Step "Verifying tool versions"
$missing = @()

# Git
try {
    $v = (& git --version 2>&1 | Out-String).Trim()
    if ($v) { Write-Ok "git : $v" } else { throw "empty" }
} catch { $missing += 'git'; Write-Fail "git not working or not on PATH" }

# Python
try {
    $pyVer = (Run-Python --version 2>&1 | Select-Object -First 1).ToString().Trim()
    if ($pyVer -match '3\.12') {
        Write-Ok "python : $pyVer"
    } else {
        Write-Warn "python : $pyVer (Expected 3.12.x)"
    }
} catch {
    $missing += 'python'
    Write-Fail "Python ($PYTHON_EXE) not working or not on PATH"
}

# Tesseract
try {
    if (Test-Cmd 'tesseract') {
        $v = (& tesseract --version 2>&1 | Select-Object -First 1).ToString().Trim()
        Write-Ok "tesseract : $v"
    } else { throw "missing" }
} catch { $missing += 'tesseract'; Write-Fail "tesseract not on PATH" }

# Ollama
try {
    if (Test-Cmd 'ollama') {
        $v = (& ollama --version 2>&1 | Out-String).Trim()
        Write-Ok "ollama : $v"
    } else { throw "missing" }
} catch { $missing += 'ollama'; Write-Fail "ollama not on PATH" }

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
        
        # Check if we have write access to $tessdata
        $testFile = Join-Path $tessdata ".write_test"
        $canWrite = $true
        try {
            New-Item -Path $testFile -ItemType File -ErrorAction Stop | Out-Null
            Remove-Item -Path $testFile -ErrorAction SilentlyContinue
        } catch {
            $canWrite = $false
        }

        if (-not $canWrite) {
            Write-Warn "No write access to system Tesseract data ($tessdata)."
            $localTessdata = Join-Path $projectRoot 'tessdata'
            Write-Info "Using local tessdata folder: $localTessdata"
            
            if (-not (Test-Path $localTessdata)) { 
                New-Item -ItemType Directory -Path $localTessdata | Out-Null 
            }

            # Copy eng.traineddata from system to local if missing (Tesseract needs both in one place)
            $systemEng = Join-Path $tessdata 'eng.traineddata'
            $localEng  = Join-Path $localTessdata 'eng.traineddata'
            if (Test-Path $systemEng) {
                if (-not (Test-Path $localEng)) {
                    Write-Info "Copying eng.traineddata to local folder..."
                    Copy-Item -Path $systemEng -Destination $localEng
                }
            } else {
                Write-Warn "System eng.traineddata not found at $systemEng. OCR may fail."
            }

            $tessdata = $localTessdata
            $hebFile  = Join-Path $tessdata 'heb.traineddata'
            
            Write-Info "Setting TESSDATA_PREFIX for this session..."
            $env:TESSDATA_PREFIX = $tessdata
        }

        try {
            if (Test-Path $hebFile) {
                Write-Ok "Hebrew traineddata present at $hebFile"
            } else {
                Write-Info "Downloading Hebrew data..."
                Invoke-WebRequest -Uri $url -OutFile $hebFile -UseBasicParsing
                Write-Ok "Downloaded $hebFile"
            }
        } catch {
            Write-Fail "Download failed: $_"
            Write-Info "Workaround: Download manually from $url and place in $tessdata"
            exit 1
        }
    } else {
        Write-Fail "Cannot proceed without Hebrew language data."
        exit 1
    }
}

# Verify tesseract sees both languages
$langs = ""
try {
    # If we are using local tessdata, we must pass it to the list-langs check too
    if ($env:TESSDATA_PREFIX) {
        $langs = (& tesseract --tessdata-dir "$($env:TESSDATA_PREFIX)" --list-langs 2>&1 | Out-String)
    } else {
        $langs = (& tesseract --list-langs 2>&1 | Out-String)
    }
} catch {
    Write-Fail "Failed to run tesseract --list-langs"
}
if ($langs -match '\bheb\b') { Write-Ok "tesseract --list-langs includes 'heb'" }
else { Write-Fail "tesseract still doesn't see 'heb'. Check $tessdata."; exit 1 }
if ($langs -match '\beng\b') { Write-Ok "tesseract --list-langs includes 'eng'" }
else { Write-Warn "tesseract --list-langs does not include 'eng' - most installs ship it by default." }

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
    
    # Wait up to 15 seconds for it to wake up
    Write-Info "Waiting for Ollama to initialize..."
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        if (Test-OllamaUp) { break }
        Write-Host "." -NoNewline
    }
    Write-Host ""

    if (Test-OllamaUp) {
        Write-Ok "Ollama now responding"
    } else {
        Write-Fail "Ollama still not responding."
        Write-Info "Please open the Ollama application manually and ensure it's running in the tray."
        exit 1
    }
}

# ---- 8) Ollama model --------------------------------------------------------
if (-not $SkipModelPull) {
    # Load model name from config.yaml if present, else default
    $configModel = "gemma4:latest"
    $configFile = Join-Path $projectRoot "config.yaml"
    if (Test-Path $configFile) {
        try {
            $configContent = Get-Content $configFile -Raw
            if ($configContent -match 'model:\s*"([^"]+)"') {
                $configModel = $Matches[1]
            }
        } catch {}
    }

    Write-Step "Checking $configModel model"
    $tags = (Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing).Content `
            | ConvertFrom-Json
    $hasModel = $tags.models | Where-Object { $_.name -eq $configModel }
    if ($hasModel) {
        Write-Ok "$configModel already pulled"
    } else {
        if (Confirm-Continue "Pull $configModel?") {
            ollama pull $configModel
            if ($LASTEXITCODE -ne 0) {
                Write-Fail "ollama pull failed (exit code $LASTEXITCODE)"
                exit 1
            }
            Write-Ok "$configModel pulled"
        } else {
            Write-Warn "Pipeline will fail at runtime without $configModel."
        }
    }
}

# ---- 9) Python venv + deps --------------------------------------------------
if (-not $SkipDeps) {
    Write-Step "Setting up Python venv at $projectRoot\.venv"

    $venvDir    = Join-Path $projectRoot '.venv'
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'

    if (Test-Path $venvPython) {
        Write-Ok ".venv already exists"
    } else {
        Run-Python -ArgsList @("-m", "venv", $venvDir)
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
            Write-Info "Extracting case_01 -> $smokeXlsx (this calls Tesseract + $configModel)..."
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
