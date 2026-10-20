# LoanReady one-command installer for Windows 10/11 (PowerShell 5.1 or later).
#
#   irm https://raw.githubusercontent.com/Mrb-Rafi/loanready/main/install.ps1 | iex
#
# What it does (no administrator rights needed):
#   1. finds Python 3.12; if it is missing, installs it with winget (per-user)
#   2. downloads LoanReady into %USERPROFILE%\loanready (C:\loanready if your user path has a
#      space); git clone if git is present, otherwise the GitHub ZIP. Re-running updates a git copy.
#   3. creates LoanReady's own Python environment (.venv) and installs its packages
#   4. creates .env from .env.example, loads the demo data (Rina's Bakery)
#   5. adds "LoanReady" shortcuts to the Start menu and the Desktop
#
# Options (set before running):
#   $env:LOANREADY_DIR = "D:\loanready"   install somewhere else
#   $env:LOANREADY_NO_START = "1"         do not start the app at the end

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster on PS 5.1

$Repo = "https://github.com/Mrb-Rafi/loanready"
$Branch = "main"

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "error: $msg" -ForegroundColor Red; throw $msg }
function Check($what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE)" } }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Install-LoanReady {
    # Install folder: short, no spaces, outside OneDrive.
    if ($env:LOANREADY_DIR) { $Dir = $env:LOANREADY_DIR }
    elseif ($HOME -notmatch " " -and $HOME -notmatch "OneDrive") { $Dir = Join-Path $HOME "loanready" }
    else { $Dir = "C:\loanready" }
    if ($Dir -match " ") { Fail "install path must not contain spaces: $Dir" }
    if ($Dir -match "OneDrive") { Fail "do not install inside OneDrive (it locks the database): $Dir" }

    # 1. Python 3.12 ----------------------------------------------------------
    function Find-Python {
        foreach ($c in @(@("py", "-3.12"), @("python3.12"), @("python"))) {
            $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
            if (Get-Command $exe -ErrorAction SilentlyContinue) {
                # try/catch: on PowerShell 5.1 a native command writing to a redirected stderr
                # (e.g. "py -3.12" when 3.12 is absent) raises an error under ErrorAction Stop.
                try {
                    $v = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
                    if ($LASTEXITCODE -eq 0 -and $v -eq "3.12") { return ,$c }
                } catch { }
            }
        }
        return $null
    }
    $Py = Find-Python
    if (-not $Py) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fail ("Python 3.12 not found and winget is unavailable. Install Python 3.12 from " +
                  "https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again.")
        }
        Say "Python 3.12 not found; installing it with winget (per-user)"
        winget install --id Python.Python.3.12 -e --scope user --silent `
            --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $Py = Find-Python
        if (-not $Py) { Fail "Python 3.12 was installed but is not on PATH yet. Close PowerShell, open a new one and run this again." }
    }
    $PyExe = $Py[0]; $PyArgs = @($Py | Select-Object -Skip 1)
    Say ("Python: " + (& $PyExe @PyArgs --version))

    # 2. Download or update ---------------------------------------------------
    if (Test-Path (Join-Path $Dir ".git")) {
        Say "Updating existing copy in $Dir"
        git -C $Dir pull --ff-only; Check "git pull"
    } elseif (Test-Path (Join-Path $Dir "tasks.py")) {
        Say "Using existing copy in $Dir (not a git checkout; not updated)"
    } elseif (Get-Command git -ErrorAction SilentlyContinue) {
        Say "Downloading LoanReady into $Dir"
        git clone --depth 1 --branch $Branch "$Repo.git" $Dir; Check "git clone"
    } else {
        Say "git not found; downloading the ZIP into $Dir"
        $tmp = Join-Path $env:TEMP ("loanready-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $tmp | Out-Null
        $zip = Join-Path $tmp "loanready.zip"
        Invoke-WebRequest "$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath $tmp
        $parent = Split-Path $Dir -Parent
        if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
        Move-Item (Join-Path $tmp "loanready-$Branch") $Dir
        Remove-Item $tmp -Recurse -Force
    }
    Set-Location $Dir

    # 3. Python environment and packages --------------------------------------
    Say "Installing LoanReady's Python packages (2-5 minutes, about 620 MB)"
    & $PyExe @PyArgs tasks.py setup; Check "setup"
    $Venv = Join-Path $Dir ".venv\Scripts\python.exe"

    # 4. Settings and demo data -----------------------------------------------
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Say "Created .env (defaults: mock AI, built-in simulator)"
    }
    # Streamlit asks for an e-mail on its first start; an empty credentials file skips that.
    $cred = Join-Path $HOME ".streamlit\credentials.toml"
    if (-not (Test-Path $cred)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $cred) | Out-Null
        Set-Content -Path $cred -Value "[general]`nemail = `"`"" -Encoding ASCII
    }
    Say "Checking the installation"
    & $Venv tasks.py doctor; Check "doctor"
    Say "Loading the demo data (about 30 seconds)"
    & $Venv tasks.py seed; Check "seed"

    # 5. Launcher and shortcuts -------------------------------------------------
    $Cmd = Join-Path $Dir "loanready.cmd"
    Set-Content -Path $Cmd -Encoding ASCII -Value @(
        "@echo off",
        "rem Start LoanReady. Open http://127.0.0.1:8501 ; Ctrl+C stops it.",
        "cd /d `"%~dp0`"",
        "if `"%~1`"==`"`" (`".venv\Scripts\python.exe`" tasks.py run) else (`".venv\Scripts\python.exe`" tasks.py %*)"
    )
    $shell = New-Object -ComObject WScript.Shell
    $startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "LoanReady.lnk"
    $desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "LoanReady.lnk"
    foreach ($lnkPath in @($startMenu, $desktop)) {
        try {
            $lnk = $shell.CreateShortcut($lnkPath)
            $lnk.TargetPath = $Cmd
            $lnk.WorkingDirectory = $Dir
            $lnk.Description = "LoanReady (sandbox demo)"
            $lnk.Save()
        } catch { Write-Host "  (could not create shortcut $lnkPath)" }
    }

    Write-Host ""
    Say "LoanReady is installed in $Dir"
    Write-Host "    Start it any time: double-click 'LoanReady' on the Desktop or in the Start menu,"
    Write-Host "    or run:  $Cmd"
    Write-Host "    Then open http://127.0.0.1:8501 in your browser. Ctrl+C in the window stops it."
    Write-Host ""

    if ($env:LOANREADY_NO_START -ne "1") {
        Say "Starting LoanReady now"
        & $Venv tasks.py ui
    }
}

Install-LoanReady
