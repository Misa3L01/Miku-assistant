# Mejoras pendientes (para ir de a una)

- [x] CI en GitHub Actions (runner Windows) con pytest + ruff (ya existe: `.github/workflows/`).
- [ ] Badge de Codecov: subir la cobertura desde el CI y agregar el badge al README.
- [ ] Leer las claves desde variables de entorno cargadas con 1Password, en vez de `config_local.py`
      (la capa de entorno ya existe; falta documentar el flujo y un comando para migrar).
- [x] Modo gaming: esperar 10 s fuera del juego antes de avisar la salida y restaurar el volumen (`BOOSTER_ESPERA_SALIDA`); la entrada sigue inmediata.
