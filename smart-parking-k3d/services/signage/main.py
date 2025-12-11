# Segnaletica parcheggi (UI) per Smart Parking
# Espone una mini-app FastAPI che mostra lo stato dei parcheggi tramite le CRD ParkingLot
import os
from typing import List, Dict, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from kubernetes import client as k8s_client
from kube import load_kube_config_safely

# Configurazione CRD e namespace
GROUP = "parking.smart"
VERSION = "v1alpha1"
NAMESPACE = os.getenv("NAMESPACE", "smart-parking")

app = FastAPI(title="Signage")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Inizializza client Kubernetes (in-cluster o kubeconfig)
load_kube_config_safely()
crd = k8s_client.CustomObjectsApi()


def list_lots_data() -> List[Dict[str, Any]]:
    """Legge i ParkingLot dal namespace, normalizza i campi e restituisce una lista semplice."""
    items: List[Dict[str, Any]] = []
    try:
        resp = crd.list_namespaced_custom_object(GROUP, VERSION, NAMESPACE, "parkinglots")
        for item in resp.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {}) or {}
            status = item.get("status", {}) or {}

            name = meta.get("name", "")
            lot_id = spec.get("lotId") or name.upper()
            total = int(spec.get("totalSpaces", 0) or 0)
            occupied = int(status.get("occupied", 0) or 0)
            free = int(status.get("free", max(0, total - occupied)) or 0)
            last_update = status.get("lastUpdate")

            items.append({
                "name": name,
                "lotId": lot_id,
                "totalSpaces": total,
                "occupied": occupied,
                "free": free,
                "lastUpdate": last_update,
            })
    except Exception:
        # In caso di errori RBAC o temporanei restituisce lista vuota e un header diagnostico
        return []
    return items


def list_spaces_data() -> List[Dict[str, Any]]:
    """Legge i ParkingSpace dal namespace e restituisce lo stato di ogni stallo."""
    items: List[Dict[str, Any]] = []
    try:
        resp = crd.list_namespaced_custom_object(GROUP, VERSION, NAMESPACE, "parkingspaces")
        for item in resp.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {}) or {}
            status = item.get("status", {}) or {}

            name = meta.get("name", "")
            lot_id = spec.get("lotId") or ""
            space_id = spec.get("spaceId") or name.upper()
            occupied = bool(status.get("occupied", False))
            sensor_online = bool(status.get("sensorOnline", False))
            last_seen = status.get("lastSeen")

            items.append(
                {
                    "name": name,
                    "lotId": lot_id,
                    "spaceId": space_id,
                    "occupied": occupied,
                    "sensorOnline": sensor_online,
                    "lastSeen": last_seen,
                }
            )
    except Exception:
        return []
    return items


def compute_summary(lots: List[Dict[str, Any]], spaces: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_lots = len(lots)
    total_spaces = sum(l.get("totalSpaces", 0) or 0 for l in lots)
    occupied_spaces = sum(l.get("occupied", 0) or 0 for l in lots)
    free_spaces = sum(l.get("free", 0) or 0 for l in lots)

    # Fallback: se non ci sono ParkingLot usiamo il conteggio diretto degli stalli
    if total_spaces == 0 and spaces:
        total_spaces = len(spaces)
        occupied_spaces = sum(1 for s in spaces if s.get("occupied"))
        free_spaces = max(0, total_spaces - occupied_spaces)

    sensors_online = sum(1 for s in spaces if s.get("sensorOnline"))
    sensors_offline = max(0, len(spaces) - sensors_online)

    return {
        "totalLots": total_lots,
        "totalSpaces": total_spaces,
        "occupiedSpaces": occupied_spaces,
        "freeSpaces": free_spaces,
        "sensorsOnline": sensors_online,
        "sensorsOffline": sensors_offline,
    }


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/lots")
def lots_json():
    return JSONResponse(list_lots_data())


@app.get("/dashboard-data")
def dashboard_data():
    lots = list_lots_data()
    spaces = list_spaces_data()
    summary = compute_summary(lots, spaces)
    return JSONResponse({"lots": lots, "spaces": spaces, "summary": summary})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})