"""
Ejecuta UN paso del pipeline y termina. Pensado para ser invocado por el
workflow de GitHub Actions cada ~10 minutos (cron). No hay ningún proceso
"corriendo siempre" — cada corrida del workflow es stateless salvo por
`estado.json`, que se lee al inicio, se actualiza, y el workflow lo
commitea de vuelta al repo al final.

Filosofía: cada llamado hace como mucho UNA transición de estado (lanzar
un kernel, chequear si terminó, chequear si llegó la aprobación de
Telegram, etc.) y sale. Si no hay nada que avanzar todavía (kernel sigue
corriendo, no llegó el /aprobado), no hace nada y el próximo cron run
vuelve a chequear.
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

USUARIO = os.environ.get("KAGGLE_USUARIO", "TU_USUARIO")
MODEL_SLUG = "audio-CNN-DEEP-rain-car"
MODEL_FRAMEWORK = "pytorch"
MODEL_INSTANCE = "modelo fundacional"
DATASET_AUDIOS = f"{USUARIO}/datos_iniciales_raw"

TOTAL_LOTES = 8  # ajustar: ~38GB del 70% / lotes de 5GB

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

REPO_ROOT = Path(__file__).parent.parent
FASE3_DIR = REPO_ROOT / "fase3_pseudolabel"
FASE4_DIR = REPO_ROOT / "fase4_finetune"
FASE5_DIR = REPO_ROOT / "fase5_full_csv"
STATE_FILE = REPO_ROOT / "estado.json"
TMP_DIR = Path("/tmp/pipeline_tmp")  # efímero del runner, no se commitea

# =========================================================
# TELEGRAM
# =========================================================

def telegram_send(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15)


def telegram_check_once(command_prefix: str, state: dict) -> bool:
    """Chequeo NO bloqueante: ¿llegó un mensaje que empiece con command_prefix
    desde el último offset guardado? Actualiza el offset en `state` siempre,
    haya o no match, para no reprocesar mensajes viejos."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    last_offset = state.get("telegram_offset", 0)
    r = requests.get(url, params={"offset": last_offset + 1, "timeout": 0}, timeout=20)
    updates = r.json().get("result", [])

    found = False
    for u in updates:
        state["telegram_offset"] = u["update_id"]
        msg = u.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "")
        if chat_id == str(TELEGRAM_CHAT_ID) and text.strip().lower().startswith(command_prefix):
            found = True
    return found


# =========================================================
# ESTADO
# =========================================================

def load_state():
    return json.loads(STATE_FILE.read_text())


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# =========================================================
# HELPERS KAGGLE CLI
# =========================================================

def run(cmd, check=True):
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(proc.stdout[-3000:])
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr)
        if check:
            raise RuntimeError(f"Falló: {' '.join(cmd)}")
    return proc.stdout


def kernel_id(kernel_dir: Path) -> str:
    meta_id = json.loads((kernel_dir / "kernel-metadata.json").read_text())["id"]
    return meta_id.replace("TU_USUARIO", USUARIO)


def kernel_status(kernel_dir: Path) -> str:
    out = run(["kaggle", "kernels", "status", kernel_id(kernel_dir)], check=False)
    m = re.search(r'"?(complete|error|cancelled|running|queued)"?', out, re.IGNORECASE)
    return m.group(1).lower() if m else "unknown"


def dataset_upsert(local_dir: Path, dataset_id: str, title: str):
    meta_path = local_dir / "dataset-metadata.json"
    run(["kaggle", "datasets", "init", "-p", str(local_dir)], check=False)
    meta = json.loads(meta_path.read_text())
    meta["id"] = dataset_id
    meta["title"] = title
    meta_path.write_text(json.dumps(meta, indent=2))
    # create o version según si ya existe
    exists = run(["kaggle", "datasets", "status", dataset_id], check=False)
    if "404" in exists or "not found" in exists.lower() or exists.strip() == "":
        run(["kaggle", "datasets", "create", "-p", str(local_dir), "--dir-mode", "zip"])
    else:
        run(["kaggle", "datasets", "version", "-p", str(local_dir), "-m", "update", "--dir-mode", "zip"])


def model_new_version(local_dir: Path, notes: str):
    instance = f"{USUARIO}/{MODEL_SLUG}/{MODEL_FRAMEWORK}/{MODEL_INSTANCE}"
    run(["kaggle", "models", "instances", "versions", "create", instance, "-p", str(local_dir), "-n", notes])


def update_kernel_metadata(kernel_dir: Path, dataset_sources=None, model_sources=None):
    meta_path = kernel_dir / "kernel-metadata.json"
    meta = json.loads(meta_path.read_text())
    if "TU_USUARIO" in meta.get("id", ""):
        meta["id"] = meta["id"].replace("TU_USUARIO", USUARIO)
    if dataset_sources is not None:
        meta["dataset_sources"] = dataset_sources
    if model_sources is not None:
        meta["model_sources"] = model_sources
    meta_path.write_text(json.dumps(meta, indent=2))


# =========================================================
# TRANSICIONES DE ESTADO (una por corrida)
# =========================================================

def paso(state):
    fase = state["fase"]
    lote = state["num_lote"]

    if fase == "fase3_por_lanzar":
        (FASE3_DIR / "lote_actual.txt").write_text(str(lote))
        update_kernel_metadata(
            FASE3_DIR,
            dataset_sources=[DATASET_AUDIOS],
            model_sources=[f"{USUARIO}/{MODEL_SLUG}/{MODEL_FRAMEWORK}/{MODEL_INSTANCE}/{state['model_version']}"],
        )
        run(["kaggle", "kernels", "push", "-p", str(FASE3_DIR)])
        telegram_send(f"🔵 Fase 3 - lote {lote}/{TOTAL_LOTES} - clasificando en Kaggle GPU")
        state["fase"] = "fase3_corriendo"

    elif fase == "fase3_corriendo":
        st = kernel_status(FASE3_DIR)
        if st == "complete":
            out_dir = TMP_DIR / f"lote_{lote}"
            run(["kaggle", "kernels", "output", kernel_id(FASE3_DIR), "-p", str(out_dir)])
            dataset_id = f"{USUARIO}/recortes-lote{lote}"
            dataset_upsert(out_dir, dataset_id, f"Recortes 5s lote {lote} (pseudo-labels)")
            telegram_send(
                f"🟡 Fase 3 - lote {lote} - LISTO. Dataset: {dataset_id}\n"
                f"1) kaggle datasets download {dataset_id} -p ./revisar --unzip\n"
                f"2) Corregí manifest.json / renombrá los .wav mal etiquetados\n"
                f"3) kaggle datasets version -p ./revisar -m \"lote {lote} revisado\" --dir-mode zip\n"
                f"4) Respondé acá:  /aprobado {lote}"
            )
            state["fase"] = "esperando_revision"
        elif st in ("error", "cancelled"):
            telegram_send(f"❌ Fase 3 lote {lote} falló ({st}). Pipeline detenido, revisá el kernel a mano.")
            state["fase"] = "detenido"
        # si sigue "running"/"queued": no hacer nada, el próximo cron vuelve a chequear

    elif fase == "esperando_revision":
        if telegram_check_once(f"/aprobado {lote}", state):
            state["fase"] = "fase4_por_lanzar"
        # si no llegó nada: no-op, se vuelve a chequear en 10 min

    elif fase == "fase4_por_lanzar":
        dataset_id = f"{USUARIO}/recortes-lote{lote}"
        update_kernel_metadata(
            FASE4_DIR,
            dataset_sources=[dataset_id],
            model_sources=[f"{USUARIO}/{MODEL_SLUG}/{MODEL_FRAMEWORK}/{MODEL_INSTANCE}/{state['model_version']}"],
        )
        run(["kaggle", "kernels", "push", "-p", str(FASE4_DIR)])
        telegram_send(f"🔵 Fase 4 - lote {lote} - fine-tuning en Kaggle GPU")
        state["fase"] = "fase4_corriendo"

    elif fase == "fase4_corriendo":
        st = kernel_status(FASE4_DIR)
        if st == "complete":
            out_dir = TMP_DIR / f"lote_{lote}_pth"
            run(["kaggle", "kernels", "output", kernel_id(FASE4_DIR), "-p", str(out_dir)])
            pth_files = list(out_dir.glob("*.pth"))
            if not pth_files:
                telegram_send(f"❌ Fase 4 lote {lote} no generó .pth. Pipeline detenido.")
                state["fase"] = "detenido"
            else:
                model_dir = TMP_DIR / "modelo_actual"
                model_dir.mkdir(parents=True, exist_ok=True)
                (model_dir / "CNN1D_DEEP.pth").write_bytes(pth_files[0].read_bytes())
                model_new_version(model_dir, notes=f"fine-tune lote {lote}")
                state["model_version"] += 1
                telegram_send(f"🟢 Fase 4 - lote {lote} - terminado. Modelo versión {state['model_version']}")
                if lote >= TOTAL_LOTES:
                    state["fase"] = "fase5_por_lanzar"
                else:
                    state["num_lote"] += 1
                    state["fase"] = "fase3_por_lanzar"
        elif st in ("error", "cancelled"):
            telegram_send(f"❌ Fase 4 lote {lote} falló ({st}). Pipeline detenido.")
            state["fase"] = "detenido"

    elif fase == "fase5_por_lanzar":
        update_kernel_metadata(
            FASE5_DIR,
            dataset_sources=[DATASET_AUDIOS],
            model_sources=[f"{USUARIO}/{MODEL_SLUG}/{MODEL_FRAMEWORK}/{MODEL_INSTANCE}/{state['model_version']}"],
        )
        run(["kaggle", "kernels", "push", "-p", str(FASE5_DIR)])
        telegram_send("🔵 Fase 5 - clasificación final del 100%, generando CSVs cada 5s")
        state["fase"] = "fase5_corriendo"

    elif fase == "fase5_corriendo":
        st = kernel_status(FASE5_DIR)
        if st == "complete":
            out_dir = TMP_DIR / "csvs_finales"
            run(["kaggle", "kernels", "output", kernel_id(FASE5_DIR), "-p", str(out_dir)])
            dataset_upsert(out_dir, f"{USUARIO}/csvs-finales-5s", "CSVs finales 5s")
            telegram_send("✅ Pipeline completo. Dataset csvs-finales-5s listo en Kaggle.")
            state["fase"] = "terminado"
        elif st in ("error", "cancelled"):
            telegram_send(f"❌ Fase 5 falló ({st}). Pipeline detenido.")
            state["fase"] = "detenido"

    elif fase in ("terminado", "detenido"):
        pass  # no-op, ya no hay nada que hacer

    return state


if __name__ == "__main__":
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    print(f"Estado actual: {state}")
    state = paso(state)
    save_state(state)
    print(f"Estado nuevo: {state}")
