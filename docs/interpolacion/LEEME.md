# Interpolación de video: revisión del `.bat` y del `.vpy`

Prueba real hecha el 19/09/2026 con `R:\VapourSynth-Env\videos\salida.mkv` (1920x1080, 23,976 fps,
7,65 s, con audio AAC y subtítulos ASS), llamando al `.bat` por el mismo camino que usa Miku
(`plugins/video_interpolador.py`).

## Resultado

| Comprobación | Resultado |
|---|---|
| Generó `salida-2x.mkv` | ✅ (4,9 MB) |
| Cuadros | ✅ 47,95 fps (= 2 × 23,976) |
| Duración | ✅ 7,65 s, idéntica a la original |
| Audio y subtítulos | ✅ AAC y ASS copiados |
| Temporales (`temp_audio.wav`, `temp_ultra.mkv`) | ✅ borrados |
| Tiempo | 39 s la primera vez; 249 s otra corrida con la GPU ocupada (la RTX 4050 tenía 5,5 de 6 GB en uso por otras apps) |

La interpolación **funciona**. Se encontraron dos defectos, ninguno impide el resultado:

## 1) El `.bat` devolvía siempre código 0 (aunque fallara)

Con un video inexistente el `.bat` original terminaba con código **0**, así que Miku decía
"¡Listo!" sin haber generado nada.

**Corrección:** `docs/interpolacion/interpolar_miku.bat` (versión revisada). Copiala a
`R:\VapourSynth-Env\Python\` reemplazando al original. Cambios (todo lo demás queda igual):

- devuelve `0` si se generó el video y `1` si falló;
- verifica que el video final exista y no esté vacío;
- borra el video final anterior antes de empezar (así "existe" significa "se generó ahora");
- limpia los temporales también cuando falla;
- si un video de la cola falla, sigue con el siguiente.

Probado: video inexistente → código 1; `salida.mkv` → código 0 y video generado.

Además, **el plugin de Miku ahora verifica por su cuenta** que exista `<nombre>-2x.mkv`, así que
aunque uses el `.bat` viejo no dirá "Listo" en falso.

## 2) El `.vpy` recorta 8 píxeles de más (salida de 1072 px en vez de 1080)

En `R:\VapourSynth-Env\Python\ultra_CAS.vpy`:

```python
clip = core.std.AddBorders(clip, bottom=8)     # 1080 -> 1088 (múltiplo de 16 para RIFE)
...
clip = core.std.Crop(clip, bottom=16)          # 1088 -> 1072   <- quita 8 px REALES de la imagen
```

La salida quedó en **1920x1072**. El recorte debe deshacer exactamente lo agregado:

```python
clip = core.std.Crop(clip, bottom=8)           # 1088 -> 1080
```

(Cambio de una línea; no lo apliqué porque el archivo es tuyo y vive fuera del proyecto.)

## Notas

- Queda un archivo `*.lwi` (índice de L-SMASH) junto al video original: es una caché normal;
  se puede borrar sin problema (la prueba lo borró).
- El `.bat` usa `enabledelayedexpansion`: un nombre de video con `!` se rompe. Miku ya rechaza
  nombres con `& % ^ ! ( ) | < > "`.
