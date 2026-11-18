# PLAN — Prueba EN→ES e interfaz para cargar y descargar el documento

> Complementa a `AGENTS.md`. Todas sus reglas siguen vigentes, sobre todo la §8 (fidelidad de formato) y la §17 (reglas para el agente).
> Trabajar por **fases**. Al terminar cada una: ejecutar `pytest -q`, mostrar resultados y **detenerse** hasta recibir confirmación.

## Objetivos

1. Comprobar que la traducción **inglés → español** funciona con el mismo rigor que español → inglés.
2. Crear una **interfaz web local en Python** donde el usuario cargue un `.docx`, elija la dirección, siga el progreso y descargue el resultado.

## Decisiones ya tomadas

| Tema | Decisión |
|---|---|
| Interfaz | **Gradio** (carga y descarga de archivos y barra de progreso integradas). Alternativa válida: Streamlit |
| Ejecución | Local, en `127.0.0.1`. No exponer a Internet |
| Clave de DeepSeek | Solo en `.env`; nunca visible ni editable desde la interfaz |
| Motor | El mismo cliente de `deepseek.py`; sin cambios en la lógica de traducción |
| Salidas | `salidas/<fecha_hora>/` con el `.docx`, el informe y la comparación; opción de descargar todo en `.zip` |

---

## Fase 0 — Extraer el núcleo reutilizable

La lógica hoy vive en `cli.py` (416 líneas). La interfaz y el CLI deben compartirla.

1. Crear `src/traductor/pipeline.py` con una función:
   ```python
   traducir_documento(
       ruta_entrada, carpeta_salida, de=None, a=None, modelo=None,
       solo=None, glosario=None, cliente="real", dry_run=False,
       on_progreso=None,   # callback(fraccion: float, mensaje: str)
   ) -> ResultadoTraduccion
   ```
2. `ResultadoTraduccion` incluye: ruta del `.docx`, del informe y de la comparación; dirección detectada; conteos; reintentos; formato simplificado; párrafos a revisar; tokens; resultado de `verificar_formato`; avisos.
3. `cli.py` pasa a ser una capa delgada que llama a `pipeline.py`. **Comportamiento idéntico al actual.**
4. `on_progreso` se invoca tras cada lote (`lote 12/44`).

**Criterio de salida:** las 74 pruebas existentes pasan; el CLI produce el mismo resultado que antes sobre el cliente falso; nueva prueba de `pipeline.py` con el cliente falso.

---

## Fase 1 — Prueba inglés → español (antes de la interfaz)

### 1.1 Preparar la dirección EN→ES
- Glosario: generar `en-es` **invirtiendo** `es-en` de `glosario.yaml`. Si dos términos en español comparten traducción al inglés, conservar el primero como preferido y reportar el conflicto. Agregar `en-es` explícito para los términos que no se invierten bien (ejemplo: `tube` → `manguera`, `casing` → `armazón`).
- Variante de destino: **español neutro latinoamericano (Perú)**. Punto decimal (`35.37`), porcentaje con espacio (`14 %`), comillas y puntuación del español.
- `prompts.py`: parametrizar variante, convenciones numéricas y reglas de etiquetas por dirección. Las reglas 4, 5, 10, 11 y 12 no cambian.
- Detectar idioma en `validar.py` con destino `es` (lingua) y el mismo rango de longitud. **Esperado: el texto crece 15–25 %.**
- La caché se separa por dirección (la clave incluye `de`, `a` y modelo).
- Mantener: título en mayúsculas si el original es literal en mayúsculas; el encabezado traduce solo la parte variable (`Manuscript in preparation 2026` → `Manuscrito en preparación 2026`); los nombres de la revista y del editor no se traducen.

### 1.2 Pruebas sin API
- Casos de validación para EN→ES (idioma, glosario invertido, números, citas, etiquetas, marcadores).
- `--dry-run` con `--de en --a es`: mostrar el prompt completo de un lote.

### 1.3 Prueba real con el documento ya traducido (ida y vuelta)
Entrada: `resultados/en/completo/FORMATO FINAL DE INSULINA_EN.docx`. Salida esperada: `..._EN_ES.docx` (la entrada nunca se modifica).

1. **Primero parcial:** `--solo P0001,P0002,P0007,P0008,P0011` y mostrar respuesta cruda, original y traducción lado a lado, validación, tokens y reintentos.
2. **Luego completa**, si la parcial es correcta.

### 1.4 Comparación contra el español original (solo informativa)
Generar `_EN_ES.comparacion_ida_vuelta.md` con, por ID: texto del original en español, texto de la ida y vuelta y una puntuación de similitud (por ejemplo `difflib.SequenceMatcher` o chrF). **No se exige coincidencia exacta**; sirve para detectar párrafos que se alejan del sentido original. Listar los 10 con menor similitud para revisión humana.

### 1.5 Comprobaciones específicas
- Términos del glosario en español: `insulinoterapia`, `dispositivo portátil`, `botón abortivo`, `registro de auditoría`, etc.
- Identificadores sin cambios: `nivel_glucosa`, `SIMULADA`, `FALLIDA`, `RF-01`.
- Citas `[1]`–`[21]` y campos Zotero y `SEQ` intactos.
- Leyendas: `Table 1` → `Tabla 1`; `Fig.` se mantiene.
- Bibliografía idéntica al original.
- `verificar_formato`: OK.
- Con texto más largo: reportar en el informe cualquier cambio de número de páginas o desplazamiento de figuras y tablas, **sin tocar fuentes, márgenes ni espaciado**.

**Criterio de salida:** 0 párrafos con formato simplificado (o justificados), 0 a revisar, `verificar_formato` OK, informe con consistencia terminológica en español y tabla de los 10 párrafos menos similares. **Detenerse y mostrar resultados.**

---

## Fase 2 — Interfaz web local

### 2.1 Archivos
```
src/traductor/interfaz.py     # app Gradio
iniciar_interfaz.bat          # doble clic en Windows
salidas/                      # resultados (en .gitignore)
```
Comando alternativo: `traductor interfaz` (con `--puerto`, por defecto 7860).

### 2.2 Pantalla
- **Cargar archivo:** solo `.docx`; tamaño máximo 50 MB (el documento actual pesa 6,9 MB).
- **Dirección:** Automática (por defecto) / Español → Inglés / Inglés → Español. Mostrar el idioma detectado y permitir cambiarlo.
- **Modelo:** `deepseek-flash` (por defecto) / `deepseek-pro`. Leer los nombres vigentes desde la configuración, no escribirlos fijos.
- **Glosario:** opcional, cargar un `.yaml`; si no, usar el del proyecto.
- **Modo:** `Traducción completa` / `Prueba parcial` (campo para IDs, ejemplo `P0001,P0002`) / `Solo estimar` (dry-run: lotes, tokens y prompt de ejemplo).
- **Botón Traducir** y barra de progreso con mensaje (`Lote 12 de 44`).
- **Resultados** al terminar:
  - Resumen: párrafos traducidos, reintentos, formato simplificado, párrafos a revisar, tokens, fidelidad de formato (OK o lista de diferencias).
  - Avisos destacados (imágenes con texto, erratas del original, desplazamiento de páginas).
  - **Descargas:** el `.docx` traducido, el informe `.md`, la comparación `.md` y un `.zip` con todo.
  - Vista previa de la comparación (tabla ES | EN | estado) con filtro «solo con problemas».
- Mensajes de error claros y en español: falta la clave, saldo insuficiente, archivo no válido, error de red.

### 2.3 Comportamiento
- Cada carga trabaja en una carpeta temporal propia dentro de `salidas/`; el original subido no se modifica.
- Una traducción a la vez (cola simple); si el usuario cierra la pestaña, el proceso termina limpiamente y la caché permite reanudar.
- Botón **Cancelar** que detiene tras el lote en curso y conserva la caché.
- Mostrar el costo estimado **antes** de traducir cuando el modo es completo y pedir confirmación si supera un umbral configurable (`UMBRAL_TOKENS`).

### 2.4 Seguridad y privacidad
- Escuchar solo en `127.0.0.1`; `share=False`.
- Validar que el archivo sea un ZIP válido con la estructura de un `.docx` (`[Content_Types].xml`, `word/document.xml`) y rechazar rutas peligrosas dentro del paquete o tamaños descomprimidos excesivos (protección contra zip bombs).
- Nunca imprimir ni mostrar la clave. No registrar el contenido del artículo en logs.
- Limpiar carpetas temporales antiguas (más de 7 días) al iniciar.

### 2.5 Pruebas
- Pruebas de `pipeline.py` con cliente falso.
- Prueba de la interfaz con el cliente de pruebas de Gradio: cargar un `.docx` pequeño, ejecutar con cliente falso, comprobar que aparecen los tres archivos y el `.zip`.
- Rechazo de archivos que no son `.docx` o están corruptos.
- Cancelación a mitad de proceso.

**Criterio de salida:** abrir la interfaz con `iniciar_interfaz.bat`, cargar `documentos/originales/FORMATO FINAL DE INSULINA.docx`, ejecutar una **prueba parcial real** en ambas direcciones y descargar los resultados. Mostrar capturas o descripción de la pantalla y el informe. **Detenerse.**

---

## Fase 3 — Cierre

1. `README.md` corto: instalación, `.env`, cómo iniciar la interfaz, cómo usar el CLI, límites conocidos.
2. Informe de uso de tokens por fase (prueba EN→ES y pruebas de interfaz).
3. Lista de limitaciones: figuras con texto, erratas del original, revisión humana final, LibreOffice opcional para comparar páginas.
4. Confirmar que `.env`, `.cache/` y `salidas/` están en `.gitignore`.

## Criterios de aceptación finales

- [ ] ES→EN y EN→ES producen un `.docx` que abre en Word sin reparación y con `verificar_formato` en OK.
- [ ] La ida y vuelta conserva citas, campos Zotero, `SEQ`, bibliografía e identificadores.
- [ ] CLI e interfaz usan el mismo `pipeline.py` y dan el mismo resultado.
- [ ] La interfaz permite cargar, elegir dirección, ver el progreso, cancelar y descargar `.docx`, informe, comparación y `.zip`.
- [ ] La clave no aparece en la interfaz, en los logs ni en los archivos de salida.
- [ ] Todas las pruebas pasan (`pytest -q`) sin llamar a la API real; las llamadas reales se limitan a las pruebas indicadas.

## Reglas para el agente en este plan

- Hacer las llamadas reales a DeepSeek **solo** en 1.3 y en el criterio de salida de la Fase 2. Antes de cada una, mostrar el dry-run con los lotes y los tokens estimados.
- No modificar la lógica de `marcado.py`, `docx_io.py` ni `verificar_formato.py` salvo que una prueba demuestre un defecto; en ese caso, explicar el cambio y agregar una prueba que lo cubra.
- Si algo contradice `AGENTS.md`, gana `AGENTS.md`.
- Al finalizar cada fase: resultados de `pytest -q`, resumen de lo hecho, incidencias y **esperar confirmación**.
