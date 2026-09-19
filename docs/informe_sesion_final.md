# Informe de sesión final — Memoria/búsqueda avanzada + integraciones (Z1–Z6)

Documenta la **tanda final** (memoria semántica, búsqueda semántica, visión,
Todoist, Telegram, asistente proactivo). El lanzador/empaquetado (Z7/Z8) queda
**gated**: se hace paso a paso con confirmación del usuario.

Reglas respetadas: arquitectura modular, lazy imports, secretos solo en
`config_local.py`, comentarios en español, logging sobre print, y `py_compile`
final OK. No se tocó el lanzador ni el empaquetado en esta fase.

## Resumen de archivos

| Archivo | Cambio |
|---|---|
| `core/embeddings.py` | **Nuevo** (Z1/Z2: motor de embeddings liviano, fastembed opcional) |
| `core/memoria.py` | Z1: búsqueda semántica sobre la misma API (LIKE de fallback) |
| `plugins/system_control.py` | Z2: re-rank semántico opcional en `buscar_archivo` |
| `plugins/vision.py` | **Nuevo** (Z3: visión de pantalla con Gemini) |
| `plugins/todoist.py` | **Nuevo** (Z4: tareas de facultad) |
| `plugins/telegram_control.py` | **Nuevo** (Z5: control remoto por Telegram) |
| `plugins/asistente_proactivo.py` | **Nuevo** (Z6: avisos de batería/disco) |
| `main.py` | Registro de 4 plugins + `bus.parser` (para Telegram) |
| `config.py` / `config_local.py.example` | Claves nuevas (embeddings, Gemini, Todoist, Telegram, proactivo) |
| `requirements.txt` | `fastembed` y `python-telegram-bot` opcionales (comentados) |

---

## Z1 — Memoria semántica
**`core/embeddings.py` + `core/memoria.py`**

- Motor liviano: **fastembed** (ONNX, sin torch). **Opcional**: si falta, la
  memoria sigue con LIKE (todo envuelto en try/except).
- `memoria.py` mantiene la MISMA API (`guardar_recuerdo/buscar_recuerdos/
  olvidar_recuerdo/cantidad/cerrar`). Se agregó (por migración) la columna
  `embedding`; al guardar se calcula el vector; al buscar, se rankea por
  similitud coseno con umbral. "cuándo es mi cumpleaños" → "nací el 15 de julio".
- RAM/deps: fastembed + onnxruntime ≈ 100-200 MB en disco; modelo en
  `data/embeddings`. **Probar:** guardar un dato y consultarlo con otras palabras.

## Z2 — Búsqueda semántica de archivos
**`plugins/system_control.py` (`_rerank_semantico`)**

- Everything sigue siendo la capa rápida de nombre/ruta. La capa semántica es un
  **re-rank** de los top-N resultados (bonus por similitud consulta vs
  nombre+carpeta). Sin motor de embeddings, devuelve igual (sin cambios).
- Empezó simple (re-rank, no índice completo). **Probar:** "buscame el PDF de
  termodinámica".

## Z3 — Visión de pantalla (Gemini)
**`plugins/vision.py`**

- Tool `ver_pantalla` (alias `que_error_me_tira`/`que_hay_en_pantalla`).
  Captura → comprime (máx 1280px, JPEG q70) → API REST `generateContent`.
- **Privacidad:** la captura se envía a Google; se avisa en la respuesta. Sin
  `GEMINI_API_KEY`, inactivo. **Probar:** "qué error me tira".

## Z4 — Todoist (tareas facultad)
**`plugins/todoist.py`**

- Tools `tareas_hoy`, `agregar_tarea`, `completar_tarea` (REST v2, Bearer).
- `TODOIST_API_TOKEN` en config_local. **Plan FREE alcanza.** Matching tolerante
  para completar; errores honestos. **Probar:** "qué tengo para hoy", "agregá
  estudiar física", "completá la tarea de física".

## Z5 — Control remoto Telegram
**`plugins/telegram_control.py`**

- Bot (`python-telegram-bot`, hilo daemon + loop propio, patrón de Discord).
  Texto del celu → **mismo parser** de la PC (vía `bus.parser`).
- Solo responde al `TELEGRAM_CHAT_ID` autorizado. **NO prende la PC.**
  Comandos `/start`, `/estado`, `/pendientes`. **Probar:** mandar "interpolá el
  último video" desde el celu.

## Z6 — Asistente proactivo (mínimo viable)
**`plugins/asistente_proactivo.py`**

- Hilo daemon que revisa batería y disco (psutil) y avisa (toast + voz) con
  **cooldown**. Sin temperatura. No invasivo. Config `PROACTIVO_*`.
- **Probar:** bajar `PROACTIVO_BATERIA_MIN` a prueba y esperar el aviso.

---

## Verificación

- `python -m compileall -q core plugins main.py config.py` → COMPILE_OK.
- **20 plugins** registran correctamente (los 16 previos + `vision`, `todoist`,
  `telegram_control`, `asistente_proactivo`).
- `memoria.py`: guardar/buscar/olvidar con la misma API (fallback LIKE sin
  fastembed).
- Sin dependencias nuevas obligatorias: fastembed y python-telegram-bot son
  **opcionales**; Gemini/Todoist usan `requests`.

## Pendiente (gated, requiere OK)
- **Z7 — Lanzador / F22 / bandeja** (incremental, 5 pasos con confirmación).
- **Z8 — Empaquetado `.exe`** (solo si Z7 estable).