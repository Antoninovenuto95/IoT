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
def index():
    # Dashboard HTML con JS che fa polling di /dashboard-data ogni 3s e visualizza tutti i dati
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Smart Parking · Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{color-scheme:dark;}
    body{margin:0;background:#0b0c0f;color:#f5f5f5;font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;min-height:100vh;display:flex;flex-direction:column}
    header{padding:28px 24px 12px;font-weight:700;font-size:28px;letter-spacing:.03em;display:flex;flex-direction:column;gap:4px}
    header span{font-size:13px;color:#9aa0a6;font-weight:500}
    main{flex:1;padding:0 24px 32px;display:flex;flex-direction:column;gap:32px}
    .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px}
    .card{background:linear-gradient(145deg,rgba(35,38,45,.96),rgba(21,23,28,.96));border-radius:18px;padding:18px 20px;box-shadow:0 18px 40px rgba(0,0,0,.35);display:flex;flex-direction:column;gap:10px}
    .card .label{font-size:13px;color:#aab2c0;letter-spacing:.04em;text-transform:uppercase}
    .card .value{font-size:36px;font-weight:700;color:#fff}
    .card .hint{font-size:12px;color:#6b7280}
    section h2{margin:0 0 12px;font-size:20px;font-weight:650;letter-spacing:.02em}
    .lot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}
    .lot-card{background:#14171f;border-radius:16px;padding:16px;display:flex;flex-direction:column;gap:6px;border:1px solid rgba(255,255,255,0.04)}
    .lot-card .lot-id{font-size:12px;color:#9aa0a6;letter-spacing:.08em}
    .lot-card .lot-free{font-size:42px;font-weight:700}
    .lot-card .lot-meta{font-size:12px;color:#9aa0a6}
    .table-wrap{overflow:auto;border-radius:16px;border:1px solid rgba(255,255,255,0.06);background:#11141b}
    table{width:100%;border-collapse:collapse;min-width:600px}
    th,td{text-align:left;padding:12px 16px;font-size:13px}
    th{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#9aa0a6;border-bottom:1px solid rgba(255,255,255,0.08)}
    tr:not(:last-child) td{border-bottom:1px solid rgba(255,255,255,0.05)}
    .status-pill{display:inline-flex;align-items:center;gap:8px;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600}
    .ok{color:#4ade80}
    .warn{color:#facc15}
    .bad{color:#f87171}
    .offline{color:#f87171}
    .online{color:#4ade80}
    .empty{opacity:.7;padding:40px;text-align:center;font-size:14px}
    footer{padding:16px 24px;color:#6b7280;font-size:12px;text-transform:uppercase;letter-spacing:.12em}
    @media (max-width:640px){
      header{padding:24px 18px 8px;font-size:22px}
      main{padding:0 18px 24px}
      .card .value{font-size:30px}
    }
  </style>
</head>
<body>
  <header>
    Smart Parking · Dashboard
    <span>Stato in tempo reale dei parcheggi, degli stalli e dei sensori</span>
  </header>
  <main>
    <section>
      <div class="cards">
        <div class="card" data-metric="totalLots">
          <div class="label">Parcheggi</div>
          <div class="value">-</div>
          <div class="hint">Totale parking lot monitorati</div>
        </div>
        <div class="card" data-metric="totalSpaces">
          <div class="label">Stalli</div>
          <div class="value">-</div>
          <div class="hint">Numero complessivo di stalli</div>
        </div>
        <div class="card" data-metric="freeSpaces">
          <div class="label">Liberi</div>
          <div class="value">-</div>
          <div class="hint">Stalli attualmente disponibili</div>
        </div>
        <div class="card" data-metric="occupiedSpaces">
          <div class="label">Occupati</div>
          <div class="value">-</div>
          <div class="hint">Stalli occupati</div>
        </div>
        <div class="card" data-metric="sensorsOnline">
          <div class="label">Sensori Online</div>
          <div class="value">-</div>
          <div class="hint">Dispositivi attivi</div>
        </div>
        <div class="card" data-metric="sensorsOffline">
          <div class="label">Sensori Offline</div>
          <div class="value">-</div>
          <div class="hint">Dispositivi non raggiungibili</div>
        </div>
      </div>
    </section>
    <section>
      <h2>Parcheggi</h2>
      <div id="lots" class="lot-grid"><div class="empty">Caricamento…</div></div>
    </section>
    <section>
      <h2>Stalli</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Parcheggio</th>
              <th>Stallo</th>
              <th>Occupazione</th>
              <th>Stato Sensore</th>
              <th>Ultimo Aggiornamento</th>
            </tr>
          </thead>
          <tbody id="spaces"><tr><td colspan="5" class="empty">Caricamento…</td></tr></tbody>
        </table>
      </div>
    </section>
  </main>
  <footer>Ultimo aggiornamento: <span id="last-update">-</span> · Aggiornamento automatico ogni 3s</footer>
<script>
const metrics = Array.from(document.querySelectorAll('.card[data-metric]')).reduce((acc, el)=>{
  acc[el.dataset.metric] = el.querySelector('.value');
  return acc;
}, {});
const lotsEl = document.getElementById('lots');
const spacesEl = document.getElementById('spaces');
const lastUpdateEl = document.getElementById('last-update');

function cls(free, total){
  if (total===0) return 'warn';
  const ratio = free/total;
  if (ratio>0.5) return 'ok';
  if (ratio>0.2) return 'warn';
  return 'bad';
}

function formatDate(value){
  if(!value) return '—';
  try {
    const d = new Date(value);
    if(!isNaN(d.getTime())){
      return d.toLocaleString();
    }
  } catch(e) {}
  return value;
}

function renderSummary(summary){
  Object.entries(summary).forEach(([key,val])=>{
    if(metrics[key]){
      metrics[key].textContent = typeof val === 'number' ? val : (val ?? '—');
    }
  });
}

function renderLots(lots){
  if(!lots || !lots.length){
    lotsEl.innerHTML = '<div class="empty">Nessun parcheggio disponibile</div>';
    return;
  }
  lotsEl.innerHTML = lots.map(l=>{
    const c = cls(l.free, l.totalSpaces);
    return `
      <div class="lot-card">
        <div class="lot-id">PARCHEGGIO ${l.lotId}</div>
        <div class="lot-free ${c}">${l.free}</div>
        <div class="lot-meta">Liberi su ${l.totalSpaces}</div>
        <div class="lot-meta">Ultimo aggiornamento: ${l.lastUpdate ? formatDate(l.lastUpdate) : '—'}</div>
      </div>`;
  }).join('');
}

function renderSpaces(spaces){
  if(!spaces || !spaces.length){
    spacesEl.innerHTML = '<tr><td colspan="5" class="empty">Nessuno stallo rilevato</td></tr>';
    return;
  }
  spacesEl.innerHTML = spaces.map(s=>{
    const occCls = s.occupied ? 'bad' : 'ok';
    const occLabel = s.occupied ? 'Occupato' : 'Libero';
    const sensorCls = s.sensorOnline ? 'online' : 'offline';
    const sensorLabel = s.sensorOnline ? 'Online' : 'Offline';
    return `
      <tr>
        <td>${s.lotId || '—'}</td>
        <td>${s.spaceId || '—'}</td>
        <td><span class="status-pill ${occCls}">${occLabel}</span></td>
        <td><span class="status-pill ${sensorCls}">${sensorLabel}</span></td>
        <td>${formatDate(s.lastSeen)}</td>
      </tr>`;
  }).join('');
}

function render(data){
  if(!data){
    lotsEl.innerHTML = '<div class="empty">Errore nel caricamento…</div>';
    spacesEl.innerHTML = '<tr><td colspan="5" class="empty">Errore nel caricamento…</td></tr>';
    return;
  }
  renderSummary(data.summary || {});
  renderLots(data.lots || []);
  renderSpaces(data.spaces || []);
  lastUpdateEl.textContent = new Date().toLocaleTimeString();
}

async function tick(){
  try{
    const r = await fetch('/dashboard-data', {cache:'no-store'});
    if(!r.ok){ throw new Error('HTTP '+r.status); }
    const data = await r.json();
    render(data);
  }catch(e){
    render(null);
  }
}

tick();
setInterval(tick, 3000);
</script>
</body>
</html>
    """
    return HTMLResponse(html)
