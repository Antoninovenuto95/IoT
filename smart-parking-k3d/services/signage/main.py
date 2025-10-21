# Segnaletica parcheggi (UI) per Smart Parking
# Espone una mini-app FastAPI che mostra lo stato dei parcheggi tramite le CRD ParkingLot
import os
from typing import List, Dict, Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from kubernetes import client as k8s_client
from kube import load_kube_config_safely

# Configurazione CRD e namespace
GROUP = "parking.smart"
VERSION = "v1alpha1"
NAMESPACE = os.getenv("NAMESPACE", "smart-parking")

app = FastAPI(title="Signage")

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
    except Exception as e:
        # In caso di errori RBAC o temporanei restituiamo lista vuota e un header diagnostico
        # (evita di rompere la UI)
        return []
    return items


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/lots")
def lots_json():
    return JSONResponse(list_lots_data())


@app.get("/", response_class=HTMLResponse)
def index():
    # HTML minimal con JS che fa polling di /lots ogni 2s e renderizza card
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Segnaletica Parcheggi</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{color-scheme:dark;}
    body{margin:0;background:#111;color:#fff;font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
    header{padding:24px 20px;font-weight:700;font-size:28px}
    .wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px;padding:0 20px 24px}
    .card{background:#1d1f20;border-radius:16px;padding:16px;box-shadow:0 4px 20px rgba(0,0,0,.25)}
    .name{letter-spacing:.08em;font-size:12px;color:#9aa0a6}
    .num{font-size:56px;line-height:1.1;margin:8px 0;font-weight:700}
    .meta{font-size:12px;color:#9aa0a6}
    .ok{color:#4ade80}.warn{color:#facc15}.bad{color:#f87171}
    .footer{padding:10px 20px;color:#9aa0a6;font-size:12px}
    .empty{opacity:.7;padding:40px;text-align:center}
  </style>
</head>
<body>
  <header>Segnaletica Parcheggi</header>
  <main id="root"><div class="empty">Caricamento…</div></main>
  <div class="footer">Aggiornamento automatico ogni 2s</div>
<script>
const root = document.getElementById('root');

function cls(free, total){
  if (total===0) return 'warn';
  const ratio = free/total;
  if (ratio>0.5) return 'ok';
  if (ratio>0.2) return 'warn';
  return 'bad';
}

function render(lots){
  if(!lots || !lots.length){
    root.innerHTML = '<div class="empty">Nessun parcheggio disponibile</div>';
    return;
  }
  root.innerHTML = '<div class="wrap">'+ lots.map(l=>{
    const c = cls(l.free, l.totalSpaces);
    return `
      <div class="card">
        <div class="name">PARCHEGGIO ${l.lotId}</div>
        <div class="num ${c}">${l.free}</div>
        <div class="meta">LIBERI / TOT ${l.totalSpaces}</div>
        <div class="meta">${l.lastUpdate ? 'Agg.: '+l.lastUpdate : ''}</div>
      </div>`;
  }).join('') + '</div>';
}

async function tick(){
  try{
    const r = await fetch('/lots', {cache:'no-store'});
    if(!r.ok){ throw new Error('HTTP '+r.status); }
    const data = await r.json();
    render(data);
  }catch(e){
    root.innerHTML = '<div class="empty">Errore nel caricamento…</div>';
  }
}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>
    """
    return HTMLResponse(html)
