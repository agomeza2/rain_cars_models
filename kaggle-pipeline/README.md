# Pipeline de fine-tuning en Kaggle, orquestado por GitHub Actions

Todo el pipeline corre solo. GitHub Actions dispara un workflow cada 10
minutos (`schedule: cron`), que revisa `estado.json`, avanza UN paso
(lanzar un kernel, chequear si terminó, chequear si aprobaste un lote por
Telegram) y sale. No hay ningún proceso que tengas que dejar corriendo en
tu máquina, y **ningún audio se procesa localmente** — el runner solo
orquesta; la clasificación, el recorte a 5s y el fine-tuning corren
dentro de kernels en la GPU de Kaggle.

El modelo de todo el pipeline es el **CNN-DEEP** (`CNN1D` con canales
`[16, 32, 64, 128]`, entrenado a 44 kHz, ver `training/train.py`).

## Setup (una sola vez)

1. **Creá un repo en GitHub** y subí esta carpeta completa tal cual
   (incluido `.github/workflows/pipeline.yml`).

2. **Agregá los secrets** en `Settings > Secrets and variables > Actions >
   New repository secret`. GitHub Actions no lee archivos `.env` — estos 4
   valores van ahí (el `.env.example` es solo referencia de qué pedir):
   - `TELEGRAM_BOT_TOKEN` — el token de tu bot (hablale a @BotFather)
   - `TELEGRAM_CHAT_ID` — tu chat id (hablale a @userinfobot, o mandale
     un mensaje a tu bot y mirá `https://api.telegram.org/bot<TOKEN>/getUpdates`)
   - `KAGGLE_USERNAME` — tu usuario de Kaggle
   - `KAGGLE_KEY` — tu API key (Kaggle > Account > Create New API Token,
     es el campo `key` del `kaggle.json` que te descarga)

3. **Identidad del modelo en Kaggle.** El orquestador referencia el modelo
   como `$KAGGLE_USERNAME/audio-CNN-DEEP-rain-car/pytorch/modelo fundacional`.
   Si tu instancia registrada usa otro framework/instancia, ajustá estas
   tres constantes en `scripts/orchestrator_step.py`:
   - `MODEL_SLUG = "audio-CNN-DEEP-rain-car"`
   - `MODEL_FRAMEWORK = "pytorch"`
   - `MODEL_INSTANCE = "modelo fundacional"`
   El `id` y los `model_sources` de los kernels se rellenan solos a partir
   de `KAGGLE_USERNAME` (los archivos pueden quedar con el placeholder
   `TU_USUARIO` commiteado).

4. **Hacé, una sola vez y a mano, lo que no se puede automatizar:**
   - Subí el dataset `datos_iniciales_raw` a Kaggle (los .wav reales,
     bajados de OneDrive local). Lo consumen las fases 3 y 5.
   - Registrá el modelo fundacional **CNN-DEEP** en Kaggle Models, usando
     la carpeta `Models/` del repo (ya contiene `CNN1D_DEEP.pth` y los
     metadatos):
     ```
     kaggle models create -p Models
     kaggle models instances create -p Models
     ```
     Eso crea la versión 1 de
     `TU_USUARIO/audio-CNN-DEEP-rain-car/pytorch/modelo fundacional`.
     Verificá que la versión tenga el `.pth` con
     `kaggle models instances versions list TU_USUARIO/audio-CNN-DEEP-rain-car/pytorch/modelo fundacional`.

5. **Ajustá `TOTAL_LOTES`** en `scripts/orchestrator_step.py` y
   `fase3_pseudolabel/pseudolabel.py` (deben coincidir) según cuántos
   lotes de ~5GB te da el 70% de tu dataset.

6. **Hacé push a la rama que tiene el workflow.** El workflow arranca solo
   con el próximo tick del cron (máximo 10 min de espera), o lo disparás a
   mano desde la pestaña **Actions > pipeline-audio-kaggle > Run workflow**.

## Qué vas a tener que hacer vos, en el medio

Solo esto, por cada lote (~8 veces en total):

1. Te llega un Telegram: *"Fase 3 - lote N - LISTO. Dataset: ..."*
2. Corrés en tu máquina los 3 comandos que te manda el mensaje (bajar,
   corregir, subir nueva versión del dataset).
3. Respondés `/aprobado N` en el mismo chat de Telegram.

El resto — lanzar kernels, esperar a que terminen, subir datasets,
versionar el modelo (`versions create` del CNN-DEEP con el `.pth`
fine-tuneado), encadenar fases — lo hace solo el workflow.

## Notas

- **Franja de audio:** los kernels usan `SAMPLE_RATE = 44000` (44 kHz), el
  mismo con el que se entrenó el CNN-DEEP. Los recortes de 5s de la fase 3
  se guardan a 44 kHz y así los lee la fase 4.
- **No toques los audios del repo**: el pipeline no los necesita; los
  audios que se clasifican viven en el dataset de Kaggle.
- **Fase 2 (OneDrive) sigue siendo manual** porque los links de los parquet
  son de SharePoint institucional y un runner de Actions no tiene tu sesión
  (no conviene poner credenciales institucionales en un secret). Si más
  adelante conseguís permisos de app registration en el tenant para usar
  Microsoft Graph API con OAuth, eso se podría automatizar también.

## Estructura

```
.github/workflows/pipeline.yml    # el cron, corre cada 10 min
scripts/orchestrator_step.py      # la lógica de un paso del pipeline
estado.json                       # estado persistido (lo actualiza el bot)
fase3_pseudolabel/                # kernel de pseudo-labeling + recorte 5s
fase4_finetune/                   # kernel de fine-tuning del CNN-DEEP
fase5_full_csv/                   # kernel de clasificación final 100%
```