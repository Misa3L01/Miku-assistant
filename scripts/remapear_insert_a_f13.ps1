# Remapea la tecla INSERT a F13 a nivel de Windows (registro: "Scancode Map").
#
#   powershell -ExecutionPolicy Bypass -File scripts\remapear_insert_a_f13.ps1            (aplica)
#   powershell -ExecutionPolicy Bypass -File scripts\remapear_insert_a_f13.ps1 -Restaurar  (deja el teclado como estaba)
#
# Necesita permisos de administrador (se pide solo, con el aviso de Windows) y REINICIAR la PC para
# que tenga efecto. Afecta a TODOS los teclados de esta PC y a todos los usuarios. Mientras esté
# aplicado, Insert ya no inserta: manda F13 (que Miku usa para invocarla, incluso con Miku cerrada,
# a través del acceso directo del Menú Inicio).
#
# Guarda una copia del valor anterior en scripts\scancode_map_anterior.txt para poder restaurar.
param([switch]$Restaurar)

$clave = 'HKLM:\SYSTEM\CurrentControlSet\Control\Keyboard Layout'
$nombre = 'Scancode Map'
$respaldo = Join-Path $PSScriptRoot 'scancode_map_anterior.txt'

$soyAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $soyAdmin) {
    $argumentos = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($Restaurar) { $argumentos += '-Restaurar' }
    Start-Process powershell -Verb RunAs -ArgumentList $argumentos -Wait
    exit
}

# Entrada del mapa: (destino = F13, scan 0x64) <- (origen = Insert, scan E0 52).
# Formato: 8 bytes en cero, cantidad de entradas + 1 (DWORD), entradas de 4 bytes (destino, origen), 0 final.
# Cada entrada se maneja como texto "64-00-52-E0" (evita que PowerShell aplane los arreglos de bytes).
$F13_DESDE_INSERT = '64-00-52-E0'
$INSERT_ORIGEN = '52-E0'          # los dos últimos bytes de la entrada: de qué tecla viene

function Nuevo-Mapa([string[]]$entradas) {
    $bytes = New-Object System.Collections.Generic.List[byte]
    $bytes.AddRange([byte[]](0,0,0,0, 0,0,0,0))
    $bytes.AddRange([BitConverter]::GetBytes([int]($entradas.Count + 1)))
    foreach ($e in $entradas) { foreach ($h in $e.Split('-')) { $bytes.Add([Convert]::ToByte($h, 16)) } }
    $bytes.AddRange([byte[]](0,0,0,0))
    return ,$bytes.ToArray()
}

function Leer-Entradas($mapa) {
    $n = [BitConverter]::ToInt32($mapa, 8) - 1
    $lista = @()
    for ($i = 0; $i -lt $n; $i++) {
        $lista += ([BitConverter]::ToString($mapa, 12 + 4*$i, 4))
    }
    return $lista
}

$actual = (Get-ItemProperty -Path $clave -Name $nombre -ErrorAction SilentlyContinue).$nombre

if ($Restaurar) {
    if ($actual) {
        $restantes = @(Leer-Entradas $actual | Where-Object { $_ -ne $F13_DESDE_INSERT })
        if ($restantes.Count -eq 0) { Remove-ItemProperty -Path $clave -Name $nombre }
        else { Set-ItemProperty -Path $clave -Name $nombre -Value (Nuevo-Mapa $restantes) -Type Binary }
    }
    Write-Host 'Insert restaurada. Reinicia la PC para que tenga efecto.'
} else {
    if ($actual -and -not (Test-Path $respaldo)) {
        ($actual | ForEach-Object { $_.ToString('X2') }) -join ' ' | Set-Content $respaldo
    }
    $entradas = @()
    if ($actual) { $entradas = @(Leer-Entradas $actual | Where-Object { -not $_.EndsWith($INSERT_ORIGEN) }) }
    $entradas += $F13_DESDE_INSERT
    Set-ItemProperty -Path $clave -Name $nombre -Value (Nuevo-Mapa $entradas) -Type Binary
    Write-Host 'Insert ahora manda F13. Reinicia la PC para que tenga efecto.'
}
Start-Sleep -Seconds 2
