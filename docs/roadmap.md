# Roadmap

Ideas evaluadas y **no implementadas todavía**, con el porqué. No son promesas: son el contexto para
quien retome el proyecto (incluido yo dentro de seis meses).

---

## 1. Respaldo automático de proveedor de IA (Groq → local)

**Qué sería.** Si Groq falla, se queda sin cuota o no hay internet, Miku pasa sola a un modelo local
(Ollama o LM Studio) y sigue respondiendo, avisando que está "en modo local".

**Por qué vale la pena.** Hoy, sin Groq, el cerebro queda mudo: solo funcionan el *fast-path* local
(hora, calculadora, memoria) y las tools que el usuario nombre exactamente. Un respaldo convierte una
caída total en una degradación parcial.

**Lo que ya está hecho.** La mitad del trabajo: `endpoint_llm()` (en `miku/cerebro/parser.py`) ya
resuelve a dónde hablarle al LLM según `LLM_BASE_URL`, y el streaming, las tools y el historial
funcionan igual contra cualquier servidor con el formato de OpenAI.

**Lo que falta.**

- Una lista de destinos en vez de uno solo (`LLM_RESPALDO_BASE_URL`, `LLM_RESPALDO_MODELO`).
- Decidir *cuándo* cambiar: HTTP 429 con `Retry-After` largo, 5xx repetidos o error de red. Conviene
  un cortacircuitos con memoria (no reintentar Groq en cada frase) y vuelta automática al principal.
- Avisar al usuario **una vez** por cambio, no en cada respuesta, y anotar el proveedor en el log de
  latencia (`miku/servicios/metricas.py`) para poder comparar.
- Aceptar que un modelo local chico entiende peor las tools: conviene bajar `ENRUTAR_MAX_TOOLS` en el
  respaldo, o directamente `LLM_SOPORTA_TOOLS = False` y conversar sin acciones.

**Esfuerzo:** medio. **Riesgo:** que el respaldo responda cosas raras y parezca que Miku "se volvió
tonta"; por eso el aviso al cambiar es parte del trabajo, no un extra.

---

## 2. Todas las claves en el Administrador de credenciales de Windows

**Qué sería.** Que `GROQ_API_KEY`, `GEMINI_API_KEY`, `DISCORD_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN` y
`TODOIST_API_TOKEN` vivan en el Administrador de credenciales (como ya vive la contraseña del
comedor) en vez de en `config_local.py`.

**Por qué vale la pena.** Hoy las claves están en texto plano en un archivo del proyecto. Está fuera
de git y eso alcanza para el uso personal, pero un archivo se copia, se comparte en un `.zip` o se
sube por error. El Administrador de credenciales las cifra con la cuenta de Windows.

**Lo que ya está hecho.** El patrón está probado en `miku/plugins/productividad/comedor.py`: `keyring`
ya es dependencia y ya hay un comando para guardar un secreto sin que se vea al escribirlo
(`... comedor guardar-clave`).

**Lo que falta.**

- Una capa en `miku/ajustes/carga.py` que, para las opciones marcadas como `"secreto"` en el esquema,
  busque en este orden: variable de entorno → Administrador de credenciales → `config_local.py`.
  Así nadie tiene que migrar nada de golpe y lo viejo sigue andando.
- Un comando `python -m miku.ajustes guardar-clave GROQ_API_KEY` que la pida sin eco y la guarde.
- Un aviso (no un error) de `python -m miku.ajustes estado` cuando una clave está en texto plano,
  ofreciendo moverla.
- Que `python -m miku.ajustes completar` no vuelva a escribir en `config_local.py` las claves que ya
  estén guardadas en el Administrador.

**Esfuerzo:** medio. **Riesgo:** bajo si se mantiene el orden de búsqueda; el peligro real es dejar
una clave solo en el Administrador y que el `.exe` empaquetado, corriendo con otra cuenta de Windows,
no la encuentre.

---

## Otras ideas más chicas

- **Wake word propia para openWakeWord.** `miku/voz/entrada/wake.py` ya soporta un modelo `.onnx`,
  pero openWakeWord no trae uno de "Miku": hay que entrenarlo con su cuaderno oficial. Con eso, la
  palabra clave se detecta sin transcribir nada.
- **Instalador y auto-actualización del `.exe`** (ver `docs/empaquetado.md`).
- **`traducir_a_canal` de Discord**, pausar Wallpaper Engine y temperatura en el Game Booster, y OCR
  de una región o ventana puntual.
