# API mobile per Smart Parking
# Espone API REST (FastAPI) per consultare lo stato dei parcheggi tramite le CRD ParkingLot
import os
from typing import List, Dict, Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from kubernetes import client as k8s_client
from kube import load_kube_config_safely

# Configurazione CRD e namespace
GROUP = "parking.smart"
VERSION = "v1alpha1"
NAMESPACE = os.getenv("NAMESPACE", "smart-parking")

app = FastAPI(title="Smart Parking Mobile API")

# Inizializza client Kubernetes (in-cluster o kubeconfig)
load_kube_config_safely()
crd = k8s_client.CustomObjectsApi()


def list_lots_data() -> List[Dict[str, Any]]:
    # Funzione per leggere e normalizzare i dati dei ParkingLot
    # Restituisce una lista di dict con info su ogni parcheggio
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
        # Manteniamo l'API sempre consistente
        return []
    return items


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    # Endpoint /health: diagnostica
    return "ok"


@app.get("/lots")
def lots():
    # Endpoint /lots: restituisce i dati dei parcheggi in JSON
    return JSONResponse(list_lots_data())
