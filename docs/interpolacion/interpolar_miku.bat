@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title === RIFE ultra_CAS (MODO MIKU AUTONOMO) ===

rem ==========================================================================
rem  interpolar_miku.bat  -  VERSION REVISADA (copiar a R:\VapourSynth-Env\Python)
rem  Cambios respecto a la original (el resto queda IGUAL):
rem    1) Devuelve un CODIGO DE SALIDA real: 0 = se genero el video, 1 = fallo.
rem       Antes siempre devolvia 0 y Miku decia "Listo" aunque fallara.
rem    2) Verifica que exista el video final y que no este vacio.
rem    3) Limpia los temporales (temp_audio.wav / temp_ultra.mkv) tambien si falla.
rem    4) Un fallo en un video no corta la cola: sigue con el siguiente.
rem ==========================================================================

set "PYTHON_PATH=R:\VapourSynth-Env\Python"
set "FFMPEG_PATH=R:\VapourSynth-Env\ffmpeg\bin\ffmpeg.exe"
set "VSPIPE=%PYTHON_PATH%\Scripts\vspipe.exe"

rem Cada argumento es la RUTA COMPLETA al video (Miku la toma de CARPETA_VIDEOS).
rem El .vpy lee el video de la variable TARGET_VIDEO y el factor de RIFE_MULTI.
set "RIFE_MULTI=2"
set "CQ=20"
set "FALLO=0"

echo Iniciando cola de renderizado automatizada...

for %%V in (%*) do (
    set "TARGET_VIDEO=%%~fV"
    set "VIDEO_DIR=%%~dpV"
    set "VIDEO_NAME=%%~nV"

    echo =============================================
    echo INICIANDO CAPITULO: %%~nxV
    echo =============================================

    rem TEMP vive en la MISMA carpeta del video de entrada.
    set "TEMP=!VIDEO_DIR!temp_ultra.mkv"
    rem OUTPUT = nombre del video SIN extension + "-2x.mkv".
    set "OUTPUT=!VIDEO_DIR!!VIDEO_NAME!-2x.mkv"

    rem Si quedo una salida vieja, se borra: asi "existe" significa "se genero AHORA".
    if exist "!OUTPUT!" del "!OUTPUT!" >nul 2>&1

    echo 1. Extrayendo audio continuo...
    "%FFMPEG_PATH%" -y -i "!TARGET_VIDEO!" -vn -sn -c:a pcm_s16le "temp_audio.wav"

    echo 2. Iniciando render de video...
    "%VSPIPE%" -c y4m "ultra_CAS.vpy" - | "%FFMPEG_PATH%" -y -i pipe:0 -c:v hevc_nvenc -preset p7 -cq %CQ% -pix_fmt p010le -an "!TEMP!"

    echo 3. Muxing final...
    "%FFMPEG_PATH%" -y -i "!TEMP!" -i "temp_audio.wav" -i "!TARGET_VIDEO!" -map 0:v -map 1:a -map 2:s? -map_chapters 2 -map_metadata 2 -c:v copy -c:a aac -b:a 320k -c:s copy "!OUTPUT!"

    del "!TEMP!" >nul 2>&1
    del "temp_audio.wav" >nul 2>&1

    rem Verificacion: el video final debe existir y no estar vacio.
    set "TAM=0"
    if exist "!OUTPUT!" for %%A in ("!OUTPUT!") do set "TAM=%%~zA"
    if "!TAM!"=="0" (
        echo ERROR: no se genero "!OUTPUT!"
        set "FALLO=1"
        if exist "!OUTPUT!" del "!OUTPUT!" >nul 2>&1
    ) else (
        echo Listo!
    )
)

exit /b !FALLO!
