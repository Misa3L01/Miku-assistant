# ============================================================================
# run.ps1 - Lanzador del asistente Miku en Windows
# ----------------------------------------------------------------------------
# 1) Activa (o crea e instala) el entorno virtual en ./venv.
# 2) Arranca VOICEVOX si no está corriendo (opcional con -SinVoicevox).
# 3) Lanza `python main.py` desde la raíz del proyecto (independiente del CWD).
#
# La decisión de "texto con o sin voz" se pregunta dentro de main.py al
# elegir el modo 3 (modo texto). Para saltarte VOICEVOX usá:  .\run.ps1 -SinVoicevox
# ============================================================================

[CmdletBinding()]
param(
    [switch]$SinVoicevox,     # --SinVoicevox: no intentar arrancar VOICEVOX
    [switch]$Reinstalar       # --Reinstalar: recrear venv e instalar deps
)

$ErrorActionPreference = "Stop"

# --- Raíz del proyecto (carpeta de este script) ---
$Raiz = $PSScriptRoot
if (-not $Raiz) { $Raiz = Get-Location }
Set-Location $Raiz

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  Miku Assistant - Lanzador" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

# ---- 1) Entorno virtual ----
$Venv = Join-Path $Raiz "venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if ($Reinstalar) {
    Write-Host "[1/3] Recreando entorno virtual..." -ForegroundColor Yellow
    if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
    python -m venv $Venv
}
if (-not (Test-Path $Python)) {
    Write-Host "[1/3] Creando entorno virtual..." -ForegroundColor Yellow
    python -m venv $Venv
    if (-not (Test-Path $Python)) {
        Write-Host "No se pudo crear venv. Instalá Python 3.10+ y reintentá." -ForegroundColor Red
        exit 1
    }
}

$FlagInstalado = Join-Path $Venv ".miku_deps_instalado"
if (-not (Test-Path $FlagInstalado) -or $Reinstalar) {
    Write-Host "[1/3] Instalando dependencias desde requirements.txt..." -ForegroundColor Yellow
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r "$Raiz\requirements.txt"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Fallo al instalar dependencias." -ForegroundColor Red
        exit 1
    }
    New-Item -ItemType File -Path $FlagInstalado -Force | Out-Null
}
Write-Host "[1/3] Entorno listo." -ForegroundColor Green

# ---- 2) VOICEVOX (opcional) ----
function Test-Voicevox {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:50021/speakers" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if (-not $SinVoicevox) {
    Write-Host "[2/3] Comprobando VOICEVOX en localhost:50021..." -ForegroundColor Yellow
    if (Test-Voicevox) {
        Write-Host "[2/3] VOICEVOX ya está activo." -ForegroundColor Green
    } else {
        $RunExe = Join-Path $Raiz "extern\VOICEVOX\vv-engine\run.exe"
        if (Test-Path $RunExe) {
            Write-Host "[2/3] Arrancando VOICEVOX (run.exe)..." -ForegroundColor Yellow
            Start-Process -FilePath $RunExe -WorkingDirectory (Split-Path $RunExe) -WindowStyle Minimized
            $ok = $false
            for ($i = 0; $i -lt 6; $i++) {
                Start-Sleep -Seconds 2
                if (Test-Voicevox) { $ok = $true; break }
            }
            if ($ok) {
                Write-Host "[2/3] VOICEVOX quedó activo." -ForegroundColor Green
            } else {
                Write-Host "[2/3] VOICEVOX no respondió a tiempo. Se usará pyttsx3." -ForegroundColor Yellow
            }
        } else {
            Write-Host "[2/3] No se halló run.exe en extern\VOICEVOX. Se usará pyttsx3." -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[2/3] VOICEVOX omitido (-SinVoicevox)." -ForegroundColor DarkGray
}

# ---- 3) Lanzar ----
Write-Host "[3/3] Lanzando python main.py ..." -ForegroundColor Green
& $Python "$Raiz\main.py"

Write-Host ""
Write-Host "Miku finalizó." -ForegroundColor Cyan
