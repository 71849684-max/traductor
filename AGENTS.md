# AGENTS.md — Traductor de artículos .docx ES ⇄ EN con DeepSeek (conserva el formato)

> Instrucciones para agentes de código (OpenCode, Claude Code, etc.).
> Objetivo: construir un CLI en Python que tome un artículo en `.docx` (formato de revista SSRG), lo divida en párrafos y secciones, lo traduzca con la API de DeepSeek y **reconstruya el mismo documento en la misma carpeta**, con el formato intacto.

## 1. Resultado esperado

```
traductor FORMATO_FINAL_DE_INSULINA.docx
→ FORMATO_FINAL_DE_INSULINA_EN.docx          (mismo formato, texto en inglés)
→ FORMATO_FINAL_DE_INSULINA_EN.informe.md    (qué se tradujo, qué se omitió, qué revisar)
```

- Dirección automática: español → inglés, o inglés → español. Se puede forzar con `--de` y `--a`.
- **El archivo original nunca se modifica.** Se trabaja sobre una copia.
- La salida queda en la misma carpeta que la entrada.

## 2. Motor: DeepSeek (única API disponible)

- API compatible con OpenAI. `base_url = https://api.deepseek.com`
- **Modelos:** usar `deepseek-v4-flash` por defecto (rápido y barato) y `deepseek-v4-pro` como opción de mayor calidad. **No usar `deepseek-chat` ni `deepseek-reasoner`**: sus nombres estaban programados para retirarse el 24/07/2026.
- Antes de codificar, verificar el nombre vigente de los modelos en https://api-docs.deepseek.com
- **La clave:** el script corre fuera de OpenCode, por lo que necesita su propia variable de entorno `DEEPSEEK_API_KEY` (la misma clave que ya usa OpenCode, de platform.deepseek.com). Nunca escribirla en el código ni subirla al repositorio.
- Cliente: paquete `openai` apuntando a DeepSeek. Reintentos con espera exponencial ante 429/5xx/timeouts.
- Temperatura baja (0.2) para traducciones consistentes. Configurable.

`.env.example`:
```
DEEPSEEK_API_KEY=
DEEPSEEK_MODELO=deepseek-v4-flash
DEEPSEEK_TEMPERATURA=0.2
LOTE_MAX_CARACTERES=4000
LOTE_MAX_PARRAFOS=12
```

## 3. Dependencias

| Herramienta | Uso |
|---|---|
| Python 3.11+ y `uv` (o `venv`) | Entorno |
| `python-docx` y `lxml` | Leer y editar el .docx sin perder estilos |
| `openai` | Cliente de la API de DeepSeek |
| `typer`, `rich` | CLI y progreso |
| `pyyaml` | Glosario |
| `lingua-language-detector` | Detectar es/en por párrafo |
| `pytest` | Pruebas |
| LibreOffice (`soffice`) | Opcional: convertir a PDF para comparar visualmente original y traducción |

## 4. Paso 0 — Inspeccionar el documento (obligatorio antes de codificar)

Crear `inspeccion.py` y ejecutarlo con el archivo real. Debe imprimir: número de párrafos, runs, tablas, imágenes/dibujos, cuadros de texto, hipervínculos, campos, notas al pie, secciones, columnas, cabeceras y pies, estilos usados y cambios con control de cambios. **Adaptar el diseño a lo que aparezca**; no asumir que el documento solo tiene párrafos simples.

Estructura conocida del artículo (por su contenido):
- Encabezado de la revista, tipo de artículo, título, autores, afiliaciones y correo (hipervínculo).
- *Abstract* y palabras clave, con texto en negrita y cursiva mezclado.
- Secciones numeradas (`1. Introducción`, `2.1. Enfoque…`) escritas como texto, no necesariamente con estilo de título.
- 4 tablas con leyenda, 4 figuras (imágenes) con leyenda.
- Etiquetas en negrita seguidas de texto en el mismo párrafo (`Monitoreo: …`).
- Citas numéricas `[1]`…`[21]` y una lista de referencias al final.
- Pie con ISSN y editorial.

## 5. Estructura del proyecto

```
traductor/
├── AGENTS.md
├── pyproject.toml
├── .env.example
├── glosario.yaml
├── src/traductor/
│   ├── cli.py            # comandos typer
│   ├── inspeccion.py     # reporte de estructura
│   ├── docx_io.py        # recorrer párrafos (cuerpo, tablas, cabeceras/pies) y reescribirlos
│   ├── marcado.py        # párrafo <-> texto con etiquetas inline
│   ├── secciones.py      # detectar secciones y zonas que no se traducen
│   ├── lotes.py          # agrupar párrafos en lotes
│   ├── deepseek.py       # cliente, reintentos y caché
│   ├── prompts.py
│   ├── validar.py
│   ├── verificar_formato.py  # compara original vs traducido (sección 8.5)
│   └── informe.py
├── .cache/               # traducciones ya hechas (se puede reanudar)
└── tests/
```

## 6. Flujo

1. **Copiar** el original a `<nombre>_<EN|ES>.docx`.
2. **Recorrer** todos los párrafos en orden de documento: cuerpo, celdas de tablas, cabeceras y pies, cuadros de texto. Asignar un ID estable (`P0001`, `T02C03`…).
3. **Detectar secciones.** Un párrafo es título si usa un estilo de título, o si es corto (menos de 120 caracteres), sin punto final y empieza con numeración (`1.`, `2.3.`) o es una palabra clave de sección (*Abstract*, *Referencias*, *Conclusiones*). Cada párrafo hereda el título de su sección como contexto.
4. **Clasificar** cada párrafo: `traducir` o `proteger` (sección 7).
5. **Serializar** cada párrafo a texto con etiquetas (sección 8).
6. **Agrupar en lotes** (sección 9) y enviarlos a DeepSeek.
7. **Validar** la respuesta (sección 11). Reintentar el lote o dividirlo si falla.
8. **Reconstruir** cada párrafo en la copia (sección 8) y cambiar el idioma de los runs (`w:lang`) al idioma destino.
9. **Guardar** el `.docx`, generar el informe y, si hay LibreOffice, un PDF de comparación.

## 7. Qué se traduce y qué no

**Se traduce:** título, *Abstract*, palabras clave, títulos de sección, párrafos, leyendas de tablas y figuras (`Tabla 1` → `Table 1`, `Fig.` se mantiene), celdas de tabla con texto descriptivo, encabezados como "Tipo de artículo" y "Manuscrito en preparación".

**No se traduce (proteger):**
- Nombres de autores, afiliaciones institucionales (`Universidad Continental`), correos, URLs, DOI.
- La **lista de referencias**: se copia tal cual. Solo se traduce el título de la sección (`Referencias` → `References`).
- Identificadores de software y de datos: `nivel_glucosa`, `evento_auditoria`, `PROGRAMADA`, `CANCELADA`, `RF-01`, nombres de tecnologías (Android Studio, Kotlin, Jetpack Compose, Room, DataStore, WorkManager, StateFlow, Bluetooth Low Energy).
- Siglas y marcas (WHO, INEI, MINSA, HbA1c, BLE).
- Números de cita `[n]`, códigos de componente (`05`, `13`), nodos (`250`, `800`, `900`).
- Pie con ISSN y editorial.
- Texto dentro de imágenes (ver sección 12).

**Errores del original:** no corregir erratas ni cambiar el contenido (por ejemplo "Worl Health Organization" o "BL│E"). Reportarlas en el informe.

## 8. Reconstrucción fiel al formato original (requisito prioritario)

**Regla de oro:** el documento traducido NO se genera desde cero. Se abre la **copia del original** y se reemplaza **únicamente el texto** de los nodos `w:t`. Todo lo demás permanece byte a byte: no se recrean párrafos, tablas ni secciones con `python-docx`, no se aplican estilos nuevos y no se normaliza el XML.

### 8.1 Qué debe quedar idéntico
- Propiedades de página y sección (`w:sectPr`): tamaño, márgenes, orientación, columnas, saltos de sección, encabezados y pies.
- Propiedades de párrafo (`w:pPr`): estilo, alineación, interlineado, sangrías, espaciado, numeración, tabulaciones, control de viudas y huérfanas.
- Propiedades de run (`w:rPr`): fuente, tamaño, color, negrita, cursiva, subrayado, superíndice, subíndice, resaltado, versalitas, espaciado de caracteres.
- Tablas: `w:tblPr`, `w:tblGrid`, `w:tcPr`, anchos, bordes, sombreado, celdas combinadas, alineación vertical.
- Objetos: imágenes y sus relaciones, posición y tamaño, cuadros de texto, formas, ecuaciones.
- Hipervínculos (destino y estilo), campos (numeración de leyendas, referencias cruzadas), marcadores, notas al pie, comentarios y control de cambios.
- Estilos, numeración, temas, fuentes incrustadas, propiedades del documento y todas las partes del paquete (`styles.xml`, `numbering.xml`, `header*.xml`, `footer*.xml`, `settings.xml`, `media/`).

### 8.2 Único cambio permitido
- El contenido de `w:t` (y `w:instrText` nunca: los campos no se tocan).
- El atributo de idioma `w:lang` de los runs traducidos (`en-US` o `es-ES`).
- División o fusión de runs **solo** cuando sea necesario para ubicar las etiquetas de formato, copiando exactamente el `rPr` del run de origen.
- Opcional: el título en `docProps/core.xml`.

### 8.3 Algoritmo
1. **Fusionar** runs adyacentes con `rPr` idéntico (sin cambiar el aspecto).
2. **Serializar** el párrafo a texto con etiquetas inline `<b>`, `<i>`, `<u>`, `<sup>`, `<sub>`, una por cada cambio de formato. Para formatos que no son esos cinco (fuente distinta, color, tamaño), usar etiquetas numeradas `<f1>…</f1>`, `<f2>…</f2>` con una tabla `f1 → rPr original` guardada en memoria.
3. **Proteger con marcadores** `⟦X01⟧`… lo que no es texto traducible: imágenes, dibujos, hipervínculos (el texto visible se traduce solo si es una frase; correos y URLs no), campos, saltos de línea, tabulaciones, notas al pie, símbolos y runs de identificadores.
4. **Traducir** y recibir el mismo conjunto de etiquetas y marcadores.
5. **Reconstruir** el párrafo: por cada segmento etiquetado, crear un `w:r` con una **copia exacta** del `rPr` correspondiente y el texto traducido (usar `xml:space="preserve"`). Insertar cada marcador restaurando su XML original en la misma posición relativa. El `pPr` del párrafo no se modifica.
6. **Respaldo** si las etiquetas no coinciden tras dos reintentos: un único run con el `rPr` del primer run de texto del párrafo; registrar `formato simplificado` en el informe.
7. **Celdas de tabla y leyendas:** misma lógica; el número de la leyenda (`Tabla 1`, `Fig. 1`) suele ser un campo o texto con formato propio: conservar su run y traducir solo la palabra.

### 8.4 Texto más largo o más corto
Al traducir de inglés a español el texto crece (alrededor de 15 a 25 %). **No reducir fuentes ni espaciado ni márgenes para compensar.** Si el contenido desborda una celda, una columna o una página, registrarlo en el informe con la página y el párrafo, y dejar que el usuario decida.

### 8.5 Verificación automática de fidelidad (obligatoria)
Implementar `verificar_formato.py` que compare original y traducido y falle si:
- **Esqueleto XML distinto:** vaciar todos los `w:t` de ambos `document.xml` (y cabeceras/pies), normalizar la partición de runs fusionando runs con `rPr` idéntico, y comparar. Deben ser iguales salvo `w:lang`.
- Cambia el número de párrafos, tablas, filas, celdas, imágenes, saltos de sección, hipervínculos, campos o marcadores.
- Cambia el contenido de `styles.xml`, `numbering.xml`, `settings.xml`, `theme`, o los archivos de `media/`.
- El multiconjunto de pares (estilo de párrafo, formato de run) cambia entre original y traducción.
- Con LibreOffice disponible: convertir ambos a PDF, comparar el número de páginas y marcar las páginas donde el diseño se desplaza. Guardar ambos PDF para comparar a la vista.

El resultado de esta verificación se incluye en el informe (`Fidelidad de formato: OK` o la lista de diferencias).

## 9. Fragmentación y contexto

- **Nunca partir un párrafo.** La unidad mínima es el párrafo; el lote agrupa párrafos consecutivos de la **misma sección**.
- Límites por lote: `LOTE_MAX_CARACTERES` y `LOTE_MAX_PARRAFOS`. Los títulos de sección viajan con el primer lote de su sección.
- Cada lote incluye como **contexto de solo lectura**: título del artículo, título de la sección actual, el glosario y el último párrafo ya traducido (para mantener el estilo y la terminología).
- Las tablas se envían celda por celda dentro de un lote por tabla, con la leyenda como contexto.
- Formato de intercambio (no JSON, para evitar errores de comillas):

```
<<<P0012>>>
texto del párrafo con <b>etiquetas</b> y ⟦X01⟧
<<<P0013>>>
siguiente párrafo
```

La respuesta debe usar exactamente los mismos delimitadores e IDs.

## 10. Prompt del sistema

Guardar en `prompts.py`, parametrizado con `{origen}`, `{destino}`, `{variante}`, `{glosario}`:

```
Eres un traductor profesional de artículos científicos de ingeniería y salud digital, {origen} → {destino}.

Tarea: traduce cada bloque delimitado por <<<ID>>>. Devuelve los mismos delimitadores e IDs, en el mismo orden, sin texto adicional.

Reglas:
1. Traduce el sentido con el estilo de un artículo científico en {variante}. Voz clara, terminología técnica precisa, sin literalismos.
2. Usa el contexto (título del artículo, sección, párrafo previo) para resolver ambigüedades y mantén el mismo término para el mismo concepto en todo el texto.
3. No agregues, quites ni resumas información. No expliques ni comentes.
4. Conserva exactamente las etiquetas <b> <i> <u> <sup> <sub> (misma cantidad, mismo orden) y los marcadores ⟦Xnn⟧ (sin traducirlos ni moverlos fuera de su frase).
5. No cambies las citas [n], los números, las unidades, los códigos (RF-01, 05, 250), ni los identificadores con guion bajo.
6. Adapta convenciones numéricas y tipográficas al idioma destino (decimales, "14 %" ↔ "14%").
7. Respeta el glosario y la lista de términos que no se traducen: {glosario}.
8. Títulos y leyendas: mantén su numeración ("2.3.", "Tabla 1" ↔ "Table 1").
9. Si un fragmento ya está en el idioma destino, devuélvelo igual.
```

Los principios de interpretación de contexto (no inventar, no omitir, mantener coherencia con el tema, producir un texto claro) ya están en las reglas 1 a 3.

## 11. Validaciones por párrafo

- Mismos delimitadores e IDs que los enviados.
- Misma cantidad de cada etiqueta y de cada marcador `⟦X…⟧`.
- Mismas citas `[n]`, mismos números y mismos identificadores protegidos.
- Idioma detectado del resultado = idioma destino (excepto párrafos protegidos o muy cortos).
- Longitud entre 0.5× y 1.8× del original.
- Términos del glosario presentes con la traducción definida.

Fallos: reintentar el lote (máximo 2 veces); si persiste, dividir el lote en párrafos individuales; si aún falla, dejar el original y añadir un comentario de Word `REVISAR` y una línea en el informe.

## 12. Glosario inicial (`glosario.yaml`)

```yaml
es-en:
  insulinoterapia: insulin therapy
  monitoreo de glucosa: glucose monitoring
  monitoreo continuo de glucosa: continuous glucose monitoring (CGM)
  sensor de glucosa intersticial: interstitial glucose sensor
  ritmo cardiaco: heart rate
  dispositivo portátil: portable device
  cuenta regresiva: countdown
  botón abortivo: abort button
  vista explotada: exploded view
  armazón: casing
  cápsula: capsule
  inyector: injector
  bomba: pump
  manguera primaria: primary tube
  manguera secundaria: secondary tube
  tiempo en rango: time in range
  automonitoreo: self-monitoring
  lapiceros conectados: connected insulin pens
  diabetes tipo 1: type 1 diabetes
  registro de auditoría: audit log
  trazabilidad: traceability
no_traducir:
  - Universidad Continental
  - Android Studio
  - Kotlin
  - Jetpack Compose
  - Room
  - DataStore
  - WorkManager
  - StateFlow
  - Bluetooth Low Energy
  - local-first
```

Para `en-es` el agente invierte las entradas y agrega términos nuevos que aparezcan. El usuario puede editar este archivo y volver a ejecutar.

## 13. Elementos que no se pueden traducir automáticamente

- **Texto dentro de imágenes** (las figuras del artículo, como el modelo de datos o la arquitectura). El script solo las detecta y las lista en el informe con su leyenda, para rehacerlas o reemplazarlas a mano.
- Gráficos o cuadros de texto como objetos flotantes: listarlos en el informe.
- Revisión final humana: una traducción científica siempre debe revisarse antes de enviarse a la revista.

## 14. CLI

```
traductor inspeccionar archivo.docx            # Paso 0: reporte de estructura
traductor archivo.docx                         # autodetecta dirección, genera _EN o _ES
traductor archivo.docx --a en --modelo deepseek-v4-pro
traductor archivo.docx --dry-run               # lotes y costo estimado, sin llamar a la API
traductor archivo.docx --reanudar              # usa la caché si se interrumpió
traductor archivo.docx --glosario otro.yaml --pdf
```

## 15. Pruebas

- Ida y vuelta sin traducir (identidad): serializar y reconstruir todo el documento; el XML debe quedar equivalente y `verificar_formato.py` debe dar OK.
- Párrafos con negritas, cursivas, superíndices, hipervínculos, campos y saltos: el `rPr` de cada segmento debe coincidir con el original.
- Celdas combinadas y leyendas con campos: estructura intacta.
- `verificar_formato.py` debe fallar cuando se altera a propósito un estilo, una tabla o una imagen.
- Detección de secciones con los títulos del artículo (`1. Introducción`, `2.1. Enfoque y alcance del diseño`, `Referencias`).
- Agrupación en lotes: ningún párrafo cortado, límites respetados, secciones no mezcladas.
- Validadores con casos que fallan a propósito (etiqueta perdida, cita alterada, marcador movido).
- Prueba de integración con un cliente falso (mock) de DeepSeek, sin llamar a la API real.
- Comando: `pytest -q`

## 16. Criterios de aceptación

- [ ] El `.docx` traducido abre en Word sin pedir reparación.
- [ ] Mismo número de párrafos, tablas e imágenes que el original; estilos, márgenes, columnas, cabeceras y pies idénticos.
- [ ] Negritas, cursivas, superíndices, tablas y leyendas conservados.
- [ ] `verificar_formato.py` reporta esqueleto XML idéntico (salvo `w:lang`) y `media/`, estilos y numeración sin cambios.
- [ ] Comparado en PDF, el número de páginas y la posición de figuras y tablas coinciden con el original, o las diferencias están listadas en el informe.
- [ ] No se alteraron tamaños de fuente, márgenes ni espaciado para ajustar la longitud del texto.
- [ ] Las citas `[1]`–`[21]` y la lista de referencias son idénticas al original.
- [ ] Los identificadores (`nivel_glucosa`, `CANCELADA`, `RF-01`…) no cambian.
- [ ] El informe lista párrafos con formato simplificado, párrafos a revisar, imágenes con texto y erratas del original.
- [ ] El original queda sin modificar y `--dry-run` no hace llamadas a la API.
- [ ] `pytest -q` pasa.

## 17. Reglas para el agente

- Empezar por el Paso 0 y por `docx_io`, `marcado` y `lotes` con pruebas; después `deepseek` y `cli`.
- Mostrar al usuario el reporte de inspección antes de seguir.
- Si hay conflicto entre traducir un fragmento y conservar el formato, **gana el formato**: dejar el fragmento sin cambios y reportarlo.
- No usar `python-docx` para crear párrafos, tablas ni secciones nuevas; solo para localizar nodos. Las modificaciones se hacen sobre el XML existente.
- No subir claves ni `.env`. No guardar el contenido del artículo en logs.
- Antes de agregar una dependencia no listada, justificarla.
- Al terminar cada etapa, ejecutar `pytest -q` y reportar el resultado.
