"""
Ejecuta UN paso del pipeline y termina.

Pensado para ser invocado por GitHub Actions cada ~10 minutos.
No hay ningún proceso corriendo siempre: cada corrida es stateless
salvo por estado.json.

Cada llamado hace como mucho UNA transición de estado:
- lanzar un kernel
- chequear si terminó
- chequear aprobación de Telegram
- procesar output
- avanzar al siguiente lote

Si no hay nada que avanzar todavía, no hace nada y el próximo
cron vuelve a comprobar.

IMPORTANTE:
Los nombres reales de los scripts son:

    fase3_pseudolabel/pseudolabel.py
    fase4_finetune/finetune.py
    fase5_full_csv/full_csv.py

Fase 3 NO utiliza lote_actual.txt.

El lote se incrusta temporalmente en pseudolabel.py mediante:

    LOTE_ACTUAL = N  # ORQUESTADOR_LOTE

El archivo modificado NO se commitea a GitHub.
Solo se utiliza para hacer el kaggle kernels push.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests


# =========================================================
# CONFIG
# =========================================================

USUARIO = os.environ.get("KAGGLE_USERNAME", "TU_USUARIO")

MODEL_SLUG = "audio-CNN-DEEP-rain-car"
MODEL_FRAMEWORK = "pytorch"
MODEL_INSTANCE = "modelo fundacional"

DATASET_AUDIOS = f"{USUARIO}/datos_iniciales_raw"

TOTAL_LOTES = 8


TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# =========================================================
# RUTAS
# =========================================================

REPO_ROOT = Path(__file__).parent.parent

FASE3_DIR = REPO_ROOT / "fase3_pseudolabel"
FASE4_DIR = REPO_ROOT / "fase4_finetune"
FASE5_DIR = REPO_ROOT / "fase5_full_csv"

FASE3_SCRIPT = FASE3_DIR / "pseudolabel.py"
FASE4_SCRIPT = FASE4_DIR / "finetune.py"
FASE5_SCRIPT = FASE5_DIR / "full_csv.py"

STATE_FILE = REPO_ROOT / "estado.json"

# Directorio temporal del runner.
# NO se commitea.
TMP_DIR = Path("/tmp/pipeline_tmp")


# =========================================================
# TELEGRAM
# =========================================================

def telegram_send(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
        },
        timeout=15,
    )


def telegram_check_once(command_prefix: str, state: dict) -> bool:
    """
    Chequeo NO bloqueante.

    Busca mensajes nuevos de Telegram desde el último offset guardado.
    Actualiza el offset aunque no encuentre el comando para no
    reprocesar mensajes viejos.
    """

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    last_offset = state.get("telegram_offset", 0)

    r = requests.get(
        url,
        params={
            "offset": last_offset + 1,
            "timeout": 0,
        },
        timeout=20,
    )

    r.raise_for_status()

    updates = r.json().get("result", [])

    found = False

    for u in updates:
        state["telegram_offset"] = u["update_id"]

        msg = u.get("message", {})

        chat_id = str(
            msg.get("chat", {}).get("id", "")
        )

        text = msg.get("text", "")

        if (
            chat_id == str(TELEGRAM_CHAT_ID)
            and text.strip().lower().startswith(
                command_prefix.lower()
            )
        ):
            found = True

    return found


# =========================================================
# ESTADO
# =========================================================

def load_state():
    if not STATE_FILE.exists():
        raise FileNotFoundError(
            f"No existe el archivo de estado: {STATE_FILE}"
        )

    return json.loads(
        STATE_FILE.read_text()
    )


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(
            state,
            indent=2,
            ensure_ascii=False,
        )
    )


# =========================================================
# HELPERS KAGGLE CLI
# =========================================================

def run(cmd, check=True):
    """
    Ejecuta un comando y muestra stdout/stderr.
    """

    print(f"$ {' '.join(cmd)}")

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if proc.stdout:
        print(proc.stdout[-5000:])

    if proc.returncode != 0:
        if proc.stderr:
            print(
                proc.stderr[-5000:],
                file=sys.stderr,
            )

        if check:
            raise RuntimeError(
                f"Falló: {' '.join(cmd)}"
            )

    return proc.stdout


def kernel_id(kernel_dir: Path) -> str:
    """
    Obtiene el ID real del kernel desde kernel-metadata.json.
    """

    meta_path = kernel_dir / "kernel-metadata.json"

    if not meta_path.exists():
        raise FileNotFoundError(
            f"No existe kernel-metadata.json: {meta_path}"
        )

    meta = json.loads(
        meta_path.read_text()
    )

    if "id" not in meta:
        raise RuntimeError(
            f"kernel-metadata.json no contiene 'id': {meta_path}"
        )

    meta_id = meta["id"]

    return meta_id.replace(
        "TU_USUARIO",
        USUARIO,
    )


def kernel_status(kernel_dir: Path) -> str:
    """
    Devuelve:

        complete
        error
        cancelled
        running
        queued
        unknown
    """

    out = run(
        [
            "kaggle",
            "kernels",
            "status",
            kernel_id(kernel_dir),
        ],
        check=False,
    )

    m = re.search(
        r'"?(complete|error|cancelled|running|queued)"?',
        out,
        re.IGNORECASE,
    )

    return (
        m.group(1).lower()
        if m
        else "unknown"
    )


# =========================================================
# DATASETS
# =========================================================

def dataset_upsert(
    local_dir: Path,
    dataset_id: str,
    title: str,
):
    """
    Crea el dataset si no existe.
    Si existe, crea una nueva versión.
    """

    local_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    meta_path = (
        local_dir / "dataset-metadata.json"
    )

    run(
        [
            "kaggle",
            "datasets",
            "init",
            "-p",
            str(local_dir),
        ],
        check=False,
    )

    if not meta_path.exists():
        raise FileNotFoundError(
            f"No se generó dataset-metadata.json en {local_dir}"
        )

    meta = json.loads(
        meta_path.read_text()
    )

    meta["id"] = dataset_id
    meta["title"] = title

    meta_path.write_text(
        json.dumps(
            meta,
            indent=2,
            ensure_ascii=False,
        )
    )

    exists = run(
        [
            "kaggle",
            "datasets",
            "status",
            dataset_id,
        ],
        check=False,
    )

    if (
        "404" in exists
        or "not found" in exists.lower()
        or exists.strip() == ""
    ):
        run(
            [
                "kaggle",
                "datasets",
                "create",
                "-p",
                str(local_dir),
                "--dir-mode",
                "zip",
            ]
        )
    else:
        run(
            [
                "kaggle",
                "datasets",
                "version",
                "-p",
                str(local_dir),
                "-m",
                "update",
                "--dir-mode",
                "zip",
            ]
        )


# =========================================================
# MODELOS
# =========================================================

def model_new_version(
    local_dir: Path,
    notes: str,
):
    instance = (
        f"{USUARIO}/"
        f"{MODEL_SLUG}/"
        f"{MODEL_FRAMEWORK}/"
        f"{MODEL_INSTANCE}"
    )

    run(
        [
            "kaggle",
            "models",
            "instances",
            "versions",
            "create",
            instance,
            "-p",
            str(local_dir),
            "-n",
            notes,
        ]
    )


# =========================================================
# KERNEL METADATA
# =========================================================

def update_kernel_metadata(
    kernel_dir: Path,
    dataset_sources=None,
    model_sources=None,
):
    """
    Actualiza kernel-metadata.json sin cambiar los nombres
    de los scripts.

    Los kernels reales son:

        fase3_pseudolabel
        fase4_finetune
        fase5_full_csv
    """

    meta_path = (
        kernel_dir / "kernel-metadata.json"
    )

    if not meta_path.exists():
        raise FileNotFoundError(
            f"No existe kernel-metadata.json: {meta_path}"
        )

    meta = json.loads(
        meta_path.read_text()
    )

    if "TU_USUARIO" in meta.get("id", ""):
        meta["id"] = meta["id"].replace(
            "TU_USUARIO",
            USUARIO,
        )

    if dataset_sources is not None:
        meta["dataset_sources"] = dataset_sources

    if model_sources is not None:
        meta["model_sources"] = model_sources

    meta_path.write_text(
        json.dumps(
            meta,
            indent=2,
            ensure_ascii=False,
        )
    )


# =========================================================
# FASE 3
# =========================================================

def preparar_script_fase3(lote: int):
    """
    Prepara fase3_pseudolabel/pseudolabel.py para el lote indicado.

    NO crea lote_actual.txt.

    Busca exactamente:

        LOTE_ACTUAL = N  # ORQUESTADOR_LOTE

    y lo reemplaza por:

        LOTE_ACTUAL = <lote>  # ORQUESTADOR_LOTE
    """

    if not FASE3_SCRIPT.exists():
        raise FileNotFoundError(
            "No existe el script de Fase 3: "
            f"{FASE3_SCRIPT}"
        )

    script = FASE3_SCRIPT.read_text()

    patron = re.compile(
        r"^\s*"
        r"LOTE_ACTUAL\s*=\s*\d+"
        r"\s*#\s*ORQUESTADOR_LOTE"
        r"\s*$",
        re.MULTILINE,
    )

    reemplazo = (
        f"LOTE_ACTUAL = {lote} "
        f"# ORQUESTADOR_LOTE"
    )

    script_nuevo, cantidad = patron.subn(
        reemplazo,
        script,
    )

    if cantidad != 1:
        raise RuntimeError(
            "No se encontró exactamente una línea "
            "'LOTE_ACTUAL = N # ORQUESTADOR_LOTE' "
            f"en {FASE3_SCRIPT}. "
            f"Encontradas: {cantidad}"
        )

    FASE3_SCRIPT.write_text(
        script_nuevo
    )

    print(
        f"Fase 3 preparada para lote {lote}"
    )

    print(
        f"Archivo: {FASE3_SCRIPT}"
    )

    print(
        f"LOTE_ACTUAL = {lote}"
    )


# =========================================================
# TRANSICIONES DE ESTADO
# =========================================================

def paso(state):
    """
    Ejecuta como máximo una transición de estado.
    """

    fase = state["fase"]
    lote = state["num_lote"]

    # =====================================================
    # FASE 3: LANZAR
    # =====================================================

    if fase == "fase3_por_lanzar":

        print(
            f"Preparando Fase 3 - lote "
            f"{lote}/{TOTAL_LOTES}"
        )

        # -------------------------------------------------
        # IMPORTANTE:
        # Ya NO se crea lote_actual.txt.
        #
        # El lote se escribe directamente dentro de
        # pseudolabel.py.
        # -------------------------------------------------

        preparar_script_fase3(lote)

        update_kernel_metadata(
            FASE3_DIR,
            dataset_sources=[
                DATASET_AUDIOS
            ],
            model_sources=[
                (
                    f"{USUARIO}/"
                    f"{MODEL_SLUG}/"
                    f"{MODEL_FRAMEWORK}/"
                    f"{MODEL_INSTANCE}/"
                    f"{state['model_version']}"
                )
            ],
        )

        # Mostrar información útil antes del push.

        print(
            "Kernel Fase 3:"
        )

        print(
            kernel_id(FASE3_DIR)
        )

        print(
            "Script Fase 3:"
        )

        print(
            FASE3_SCRIPT
        )

        # -------------------------------------------------
        # PUBLICAR EN KAGGLE
        # -------------------------------------------------

        run(
            [
                "kaggle",
                "kernels",
                "push",
                "-p",
                str(FASE3_DIR),
            ]
        )

        telegram_send(
            f"🔵 Fase 3 - lote "
            f"{lote}/{TOTAL_LOTES} - "
            f"clasificando en Kaggle GPU"
        )

        state["fase"] = (
            "fase3_corriendo"
        )

    # =====================================================
    # FASE 3: CORRIENDO
    # =====================================================

    elif fase == "fase3_corriendo":

        st = kernel_status(
            FASE3_DIR
        )

        print(
            f"Estado kernel Fase 3: {st}"
        )

        if st == "complete":

            out_dir = (
                TMP_DIR / f"lote_{lote}"
            )

            out_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            run(
                [
                    "kaggle",
                    "kernels",
                    "output",
                    kernel_id(FASE3_DIR),
                    "-p",
                    str(out_dir),
                ]
            )

            dataset_id = (
                f"{USUARIO}/recortes-lote{lote}"
            )

            dataset_upsert(
                out_dir,
                dataset_id,
                f"Recortes 5s lote {lote} "
                "(pseudo-labels)",
            )

            telegram_send(
                f"🟡 Fase 3 - lote {lote} - "
                f"LISTO. Dataset: {dataset_id}\n"
                f"1) kaggle datasets download "
                f"{dataset_id} -p ./revisar --unzip\n"
                f"2) Corregí manifest.json / "
                f"renombrá los .wav mal etiquetados\n"
                f"3) kaggle datasets version "
                f"-p ./revisar -m "
                f"\"lote {lote} revisado\" "
                f"--dir-mode zip\n"
                f"4) Respondé acá: "
                f"/aprobado {lote}"
            )

            state["fase"] = (
                "esperando_revision"
            )

        elif st in (
            "error",
            "cancelled",
        ):

            telegram_send(
                f"❌ Fase 3 lote {lote} "
                f"falló ({st}). "
                f"Pipeline detenido, "
                f"revisá el kernel a mano."
            )

            state["fase"] = "detenido"

        # running / queued:
        # no hacer nada.
        #
        # El siguiente cron vuelve a comprobar.

    # =====================================================
    # ESPERANDO APROBACIÓN
    # =====================================================

    elif fase == "esperando_revision":

        if telegram_check_once(
            f"/aprobado {lote}",
            state,
        ):

            print(
                f"Aprobación recibida para lote {lote}"
            )

            state["fase"] = (
                "fase4_por_lanzar"
            )

    # =====================================================
    # FASE 4: LANZAR
    # =====================================================

    elif fase == "fase4_por_lanzar":

        dataset_id = (
            f"{USUARIO}/recortes-lote{lote}"
        )

        print(
            f"Preparando Fase 4 - lote {lote}"
        )

        update_kernel_metadata(
            FASE4_DIR,
            dataset_sources=[
                dataset_id
            ],
            model_sources=[
                (
                    f"{USUARIO}/"
                    f"{MODEL_SLUG}/"
                    f"{MODEL_FRAMEWORK}/"
                    f"{MODEL_INSTANCE}/"
                    f"{state['model_version']}"
                )
            ],
        )

        print(
            f"Script Fase 4: {FASE4_SCRIPT}"
        )

        run(
            [
                "kaggle",
                "kernels",
                "push",
                "-p",
                str(FASE4_DIR),
            ]
        )

        telegram_send(
            f"🔵 Fase 4 - lote {lote} - "
            f"fine-tuning en Kaggle GPU"
        )

        state["fase"] = (
            "fase4_corriendo"
        )

    # =====================================================
    # FASE 4: CORRIENDO
    # =====================================================

    elif fase == "fase4_corriendo":

        st = kernel_status(
            FASE4_DIR
        )

        print(
            f"Estado kernel Fase 4: {st}"
        )

        if st == "complete":

            out_dir = (
                TMP_DIR
                / f"lote_{lote}_pth"
            )

            out_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            run(
                [
                    "kaggle",
                    "kernels",
                    "output",
                    kernel_id(FASE4_DIR),
                    "-p",
                    str(out_dir),
                ]
            )

            pth_files = list(
                out_dir.glob("*.pth")
            )

            if not pth_files:

                telegram_send(
                    f"❌ Fase 4 lote {lote} "
                    f"no generó .pth. "
                    f"Pipeline detenido."
                )

                state["fase"] = (
                    "detenido"
                )

            else:

                model_dir = (
                    TMP_DIR
                    / "modelo_actual"
                )

                model_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                (
                    model_dir
                    / "CNN1D_DEEP.pth"
                ).write_bytes(
                    pth_files[0].read_bytes()
                )

                model_new_version(
                    model_dir,
                    notes=(
                        f"fine-tune lote {lote}"
                    ),
                )

                state["model_version"] += 1

                telegram_send(
                    f"🟢 Fase 4 - lote {lote} - "
                    f"terminado. Modelo versión "
                    f"{state['model_version']}"
                )

                if lote >= TOTAL_LOTES:

                    state["fase"] = (
                        "fase5_por_lanzar"
                    )

                else:

                    state["num_lote"] += 1

                    state["fase"] = (
                        "fase3_por_lanzar"
                    )

        elif st in (
            "error",
            "cancelled",
        ):

            telegram_send(
                f"❌ Fase 4 lote {lote} "
                f"falló ({st}). "
                f"Pipeline detenido."
            )

            state["fase"] = (
                "detenido"
            )

    # =====================================================
    # FASE 5: LANZAR
    # =====================================================

    elif fase == "fase5_por_lanzar":

        print(
            "Preparando Fase 5"
        )

        update_kernel_metadata(
            FASE5_DIR,
            dataset_sources=[
                DATASET_AUDIOS
            ],
            model_sources=[
                (
                    f"{USUARIO}/"
                    f"{MODEL_SLUG}/"
                    f"{MODEL_FRAMEWORK}/"
                    f"{MODEL_INSTANCE}/"
                    f"{state['model_version']}"
                )
            ],
        )

        print(
            f"Script Fase 5: {FASE5_SCRIPT}"
        )

        run(
            [
                "kaggle",
                "kernels",
                "push",
                "-p",
                str(FASE5_DIR),
            ]
        )

        telegram_send(
            "🔵 Fase 5 - clasificación final "
            "del 100%, generando CSVs cada 5s"
        )

        state["fase"] = (
            "fase5_corriendo"
        )

    # =====================================================
    # FASE 5: CORRIENDO
    # =====================================================

    elif fase == "fase5_corriendo":

        st = kernel_status(
            FASE5_DIR
        )

        print(
            f"Estado kernel Fase 5: {st}"
        )

        if st == "complete":

            out_dir = (
                TMP_DIR
                / "csvs_finales"
            )

            out_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            run(
                [
                    "kaggle",
                    "kernels",
                    "output",
                    kernel_id(FASE5_DIR),
                    "-p",
                    str(out_dir),
                ]
            )

            dataset_upsert(
                out_dir,
                f"{USUARIO}/csvs-finales-5s",
                "CSVs finales 5s",
            )

            telegram_send(
                "✅ Pipeline completo. "
                "Dataset csvs-finales-5s "
                "listo en Kaggle."
            )

            state["fase"] = (
                "terminado"
            )

        elif st in (
            "error",
            "cancelled",
        ):

            telegram_send(
                f"❌ Fase 5 falló ({st}). "
                f"Pipeline detenido."
            )

            state["fase"] = (
                "detenido"
            )

    # =====================================================
    # DETENIDO / TERMINADO
    # =====================================================

    elif fase in (
        "terminado",
        "detenido",
    ):

        print(
            f"Pipeline en estado '{fase}'. "
            f"No hay nada que hacer."
        )

    else:

        raise RuntimeError(
            f"Fase desconocida en estado.json: {fase}"
        )

    return state


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    TMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    state = load_state()

    print(
        f"Estado actual: {state}"
    )

    state = paso(state)

    save_state(state)

    print(
        f"Estado nuevo: {state}"
    )
