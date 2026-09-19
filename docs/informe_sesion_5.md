# Informe de sesión 5 — Tanda intermedia (bloques I1–I10)

Sesión enfocada en las capacidades "intermedias": navegador por CDP, TIDAL,
salir de modos, recordatorios a hora exacta, clima, personalidad, toasts,
Auto-Game Booster, OCR y briefing. Misma arquitectura modular, **lazy imports**,
secretos solo en `config_local.py`, comentarios en español, logging sobre
`print`, y `py_compile` de todo al final. **No** se tocó el lanzador ni el
empaquetado.

## Resumen de archivos

| Archivo | Cambio |
|---|---|
| `plugins/browser.py` | **Nuevo** (I1: Brave por CDP) |
| `plugins/tidal.py` | **Nuevo** (I2: TIDAL + SMTC) |
| `core/modos.py` | **Nuevo** (I3: snapshots/reversión) |
| `plugins/modos.py` | **Nuevo** (I3: `salir_modo`) |
| `plugins/macros.py` | I3: captura snapshot antes de la macro |
| `plugins/system_control.py` | I4: `programar_accion` con `hora` |
| `plugins/clima.py` | **Nuevo** (I5: Open-Meteo) |
| `core/personalidad.py` | **Nuevo** (I6: perfiles) |
| `plugins/personalidad.py` | **Nuevo** (I6: cambiar por voz) |
| `core/command_parser.py` | I6: inyecta el prompt del perfil |
| `core/notificaciones.py` | **Nuevo** (I7: toasts) |
| `plugins/game_booster.py` | **Nuevo** (I8: auto-game booster) |
| `plugins/ocr.py` | **Nuevo** (I9: OCR de pantalla) |
| `core/briefing.py` | **Nuevo** (I10: saludo con contexto) |
| `main.py` | Registro de 7 plugins nuevos + briefing + adorno de personalidad |
| `config.py` / `config_local.py.example` | Claves nuevas (clima, personalidad, booster, OCR) |
| `requirements.txt` | `pytesseract` opcional (comentado) |

---

## I1 — Navegador Brave por CDP
**`plugins/browser.py`** — Tools `abrir_pestana`, `cerrar_pestana`,
`buscar_en_pestana_actual`. Usa el **HTTP JSON API** del puerto de depuración
(`brave_debug_port`, default 9222); si no está, lanza Brave con
`brave_ruta_exe` + `--remote-debugging-port`. Mensaje claro si no puede
conectar/lanzar. **Probar:** “abrí una pestaña de YouTube”.

## I2 — Control fino de TIDAL
**`plugins/tidal.py`** — Tools `controlar_tidal` (play/pausa/siguiente/anterior
por teclas multimedia del sistema) y `que_esta_sonando` (Windows SMTC). Se
documenta el **límite real**: TIDAL no expone API pública. **Probar:** “pausá
TIDAL”, “qué está sonando”.

## I3 — Salir de modos (revertir)
**`core/modos.py` + `plugins/modos.py`** — `capturar()` guarda un snapshot
(volumen general/resolución/brillo); `salir_modo` lo revierte. `macros.py`
captura ANTES de aplicar la macro. Si no hay snapshot, avisa sin romper.
**Probar:** “salí del modo”.

## I4 — Recordatorios a hora exacta
**`plugins/system_control.py`** — `programar_accion` acepta `hora` (HH:MM, “18h”
o “18”); `_segundos_hasta_hora` calcula el delta y, si la hora ya pasó hoy,
asume mañana. Cancelar sigue igual. **Probar:** “recordame a las 18:30 que
salgo”.

## I5 — Clima (Open-Meteo)
**`plugins/clima.py`** — Tool `clima`: geocodifica `CIUDAD_CLIMA` (o usa
`CLIMA_LAT/LON`) y arma una frase **natural** con recomendación. Sin API key.
**Probar:** “¿cómo está el clima?”.

## I6 — Personalidad configurable
**`core/personalidad.py` + `plugins/personalidad.py`** — Perfiles neutral,
tsundere, formal y entusiasta. El perfil aporta un `prompt` (se inyecta en el
system prompt del LLM vía `BrainGroq`) y un **adorno** para respuestas cortas
(`main.responder()`). Tools `cambiar_personalidad` y `listar_personalidades`;
persistible en `preferences.json`. **Probar:** “hablá más tsundere”, “modo
formal”.

## I7 — Confirmación visual (toasts)
**`core/notificaciones.py`** — `notificar(titulo, mensaje)` no bloqueante
(hilo), con backends en orden: `win10toast` → PowerShell + WinRT → log. Escapa
XML para no romper el script. **Uso:** interpolación, recordatorio, errores,
Game Booster.

## I8 — Auto-Game Booster
**`plugins/game_booster.py`** — Hilo daemon que vigila el **primer plano**
(proceso vía win32 + psutil). Si el proceso está en `JUEGOS_BOOSTER`: snapshot +
bajar volumen de `BOOSTER_APPS_VOLUMEN` + aviso (toast/voz). Al salir del juego,
revierte si hay snapshot. **Sin spam** (solo transiciones). **Probar:** abrir un
juego configurado.

## I9 — OCR de pantalla
**`plugins/ocr.py`** — Tool `leer_pantalla`. Motor elegido: **Tesseract**
(pytesseract, primario, paquete `spa`) con **fallback a Windows.Media.Ocr**
(WinRT) sin instalar nada. Captura vía `PIL.ImageGrab`; todo lazy/best-effort.
**Probar:** “leé la pantalla”, “qué dice esta ventana”.

## I10 — Briefing al activar modo voz
**`core/briefing.py`** — `run_modo_voz` reemplaza el “Ya estoy lista” seco por
una frase con **hora + clima (si hay) + recordatorios pendientes** (del
Scheduler). **Probar:** entrar en modo voz.

---

## Verificación

- `python -m compileall -q core plugins main.py config.py` → COMPILE_OK.
- 16 plugins registran correctamente (los 9 previos + 7 nuevos).
- Personalidad: `cambiar_perfil('tsundere')` OK; `adorno_corto('Listo.')` →
  agrega coletilla.
- Hora exacta: `_segundos_hasta_hora('18:30'/'08'/'18h')` → válidos; `'xx'` →
  None.
- Briefing: `generar({})` → “¡Buenas tardes! Soy Miku, son las 12:01.”

## Notas de diseño
- **Sin dependencias nuevas obligatorias:** CDP/TIDAL/OCR/booster usan lo que ya
  había (requests, win32, psutil, Pillow) o backends nativos de Windows
  (PowerShell/WinRT). `pytesseract` queda **opcional** (comentado) por el OCR
  nativo de Windows.
- **Límites documentados honestamente:** TIDAL (sin API pública) y OCR
  (depende de motor/idioma). Nada se “arregló” que sea comportamiento del SO/la app.