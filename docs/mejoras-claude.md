# Mejoras pendientes (para ir de a una)

- [x] CI en GitHub Actions (runner Windows) con pytest + ruff (ya existe: `.github/workflows/`).
- [ ] Badge de Codecov: subir la cobertura desde el CI y agregar el badge al README.
- [ ] Leer las claves desde variables de entorno cargadas con 1Password, en vez de `config_local.py`
      (la capa de entorno ya existe; falta documentar el flujo y un comando para migrar).
- [x] Modo gaming: esperar 10 s fuera del juego antes de avisar la salida y restaurar el volumen (`BOOSTER_ESPERA_SALIDA`); la entrada sigue inmediata.
- [x] Preguntas de confirmación cortas ("¿Lo hago?") y respuesta por voz sin decir "Miku" (el micrófono se abre unos segundos tras la pregunta).
- [ ] Renombrar y copiar archivos en lote, con confirmación y deshacer (pospuesto a pedido de Misael).
- [x] Registro de uso por programa (solo nombres y minutos) + "¿en qué gasté el tiempo?" + aviso cada 2 h de juego seguido (`USO_AVISO_HORAS`).
- [x] Aprobar desde el celular: si Miku pregunta y no contestás en 20 s, la pregunta llega por Telegram con botones Sí/No.
- [x] Control de hábitos: si ves una página/palabra de tu lista (o incógnito), Miku te rezonga con una ruleta de frases y pregunta "¿La cierro?".
- [ ] Ideas guardadas: foto o captura (Telegram o "guardá la última captura") + recordatorio con botones (mañana / finde / semana).
