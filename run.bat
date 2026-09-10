@echo off
rem ==========================================================================
rem  run.bat - Lanzador del asistente Miku en Windows (cmd)
rem --------------------------------------------------------------------------
rem  1) Activa o crea el entorno virtual en .\venv
rem  2) Arranca VOICEVOX si no está corriendo
rem  3) Lanza python main.py desde la raíz del proyecto
rem
rem  La decisión "texto con/sin voz" se pregunta dentro de main.py al modo 3.
rem  Para saltarte VOICEVOX ejecutá:  run.bat sinvoicevox
rem ==========================================================================

setlocal
cd /d "%~dp0"

echo.
echo ======================================
echo   Miku Assistant - Lanzador
echo ======================================

set "PY=.\venv\Scripts\python.exe"
set "VOICEVOX_RUN=.\extern\VOICEVOX\vv-engine\run.exe"

rem ---------- 1) Entorno virtual ----------
if not exist "%PY%" (
    echo [1/3] Creando entorno virtual...
    python -m venv venv
    if not exist "%PY%" (
        echo No se pudo crear venv. Instalá Python 3.10+ y reintentá.
        exit /b 1
    )
)

if not exist ".\venv\.miku_deps_instalado" (
    echo [1/3] Instalando dependencias desde requirements.txt...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r ".\requirements.txt"
    if errorlevel 1 (
        echo Fallo al instalar las dependencias.
        exit /b 1
    )
    echo marcador_deps > ".\venv\.miku_deps_instalado"
)
echo [1/3] Entorno listo.

rem ---------- 2) VOICEVOX (opcional con arg "sinvoicevox") ----------
if /I not "%1"=="sinvoicevox" (
    echo [2/3] Comprobando VOICEVOX en localhost:50021...
    powershell -NoProfile -Command ^
      "try { $r=Invoke-WebRequest -Uri 'http://localhost:50021/speakers' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }"
    if errorlevel 1 (
        if exist "%VOICEVOX_RUN%" (
            echo [2/3] Arrancando VOICEVOX ^(run.exe^)...
            start "" "%VOICEVOX_RUN%"
            powershell -NoProfile -Command ^
              "$ok=$false; for($i=0;$i -lt 6;$i++){Start-Sleep -Seconds 2; try{$r=Invoke-WebRequest -Uri 'http://localhost:50021/speakers' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){$ok=$true;break}}catch{}}; exit $(if($ok){0}else{1})"
            if errorlevel 1 ( echo [2/3] VOICEVOX no respondio a tiempo. Fallback pyttsx3. ) else ( echo [2/3] VOICEVOX activo. )
        ) else (
            echo [2/3] No se encontro run.exe. Fallback pyttsx3.
        )
    ) else (
        echo [2/3] VOICEVOX ya estaba activo.
    )
) else (
    echo [2/3] VOICEVOX omitido.
)

rem ---------- 3) Lanzar ----------
echo [3/3] Lanzando python main.py ...
"%PY%" main.py

echo.
echo Miku finalizo.
endlocal
