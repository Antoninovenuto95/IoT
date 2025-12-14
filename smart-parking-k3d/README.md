# Smart Parking su Kubernetes (k3d) — **README completo**

Questo repository contiene un ambiente dimostrativo di *Smart Parking* basato su **MQTT** e **Custom Resource Definitions (CRD)** in Kubernetes. Include:
- Quattro servizi principali (simulatore sensori, aggregatore, segnaletica web, API mobile)
- Un broker **Eclipse Mosquitto** configurato per **TLS/mTLS**
- Manifest Kubernetes (CRD, RBAC, Deployment/Service)
- Un componente **WASM Aggregator** (SpinKube) per aggregazione MQTT → CRD via WebAssembly

> **TL;DR**
> - Crea un cluster k3d e un namespace `smart-parking`
> - Applica CRD + Mosquitto
> - Builda le 4 immagini Docker e importale nel cluster
> - Crea i Secret TLS/mTLS (CA/servizio/client)
> - Applica RBAC + Deployment/Service dei 4 componenti
> - Applica il Deployment dell'aggregatore WASM
> - `kubectl port-forward` per vedere **Signage** (UI) e **Mobile API**

---

## Struttura del repository (principale)



```
smart-parking-k3d/
├── README.md
│   # Documentazione principale del progetto
├── build_deploy_all.cmd
│   # Script batch Windows: build, segreti, deploy, port-forward (tutto in uno)
├── k8s/
│   ├── crds/
│   │   ├── parkinglot-crd.yaml         # Definizione CRD ParkingLot
│   │   └── parkingspace-crd.yaml       # Definizione CRD ParkingSpace
│   ├── mosquitto/
│   │   ├── configmap.yaml              # Configurazione Mosquitto
│   │   ├── deployment.yaml             # Deployment Mosquitto
│   │   └── service.yaml                # Service Mosquitto
│   │── tls/
│   │   └── tls-secrets.yaml            # Certificati e chiavi TLS
│   └── rbac-signage-mobile.yaml        # Permessi per FastAPI UI
├── services/
│   ├── sensor-simulator/
│   │   ├── Dockerfile                  # Build container simulatore sensori
│   │   ├── main.py                     # Codice simulatore sensori
│   │   ├── kube.py                     # Utility Kubernetes client
│   │   ├── deployment.yaml             # Deployment simulatore sensori
│   │   └── requirements.txt            # Dipendenze Python
│   ├── aggregator/
│   │   ├── Dockerfile                  # Build container aggregator
│   │   ├── main.py                     # Codice aggregator
│   │   ├── kube.py                     # Utility Kubernetes client
│   │   ├── deployment.yaml             # Deployment aggregator
│   │   └── requirements.txt            # Dipendenze Python
│   ├── signage/
|   |   ├── static/
│   │   |   ├── dashboard.js            # Funzioni JavaScript dashboard
|   |   |   └── style.css               # Foglio di stile dashboard
|   |   ├── templates/
|   |   |   └── index.html              # Dashboard del sistema
│   │   ├── Dockerfile                  # Build container UI
│   │   ├── main.py                     # Codice FastAPI UI
│   │   ├── kube.py                     # Utility Kubernetes client
│   │   ├── deployment.yaml             # Deployment FastAPI UI
│   │   └── requirements.txt            # Dipendenze Python
│   └── mobile-api/
│       ├── Dockerfile                  # Build container API mobile
│       ├── main.py                     # Codice FastAPI API mobile
│       ├── kube.py                     # Utility Kubernetes client
│       ├── deployment.yaml             # Deployment FastAPI API mobile
│       └── requirements.txt            # Dipendenze Python
├── wasm-aggregator/
│   ├── src/
│   │   └── lib.rs                      # Codice sorgente Rust: logica aggregazione MQTT → CRD
│   ├── spin.toml                       # Configurazione SpinKube: trigger MQTT, variabili ambiente, path CRD
│   ├── Cargo.toml                      # Configurazione progetto Rust (dipendenze, build)
│   ├── Cargo.lock                      # Lock file dipendenze Rust
│   ├── kubeapi-haproxy.yaml            # Manifest HAProxy verso l’API server del cluster
│   ├── rbac-wasm-aggregator.yaml       # Permessi per WASM aggregator
│   └── spinapp.yaml                    # Manifest SpinKube app
```

Ogni cartella contiene file specifici per la sua funzione: manifest, codice, configurazioni e script per deployment e sicurezza.

Ogni cartella/folder è pensata per isolare una componente del sistema:
- **k8s/** contiene tutto ciò che serve per la configurazione e la sicurezza su Kubernetes
- **services/** racchiude i microservizi Python/FastAPI
- **wasm-aggregator/** permette di usare un aggregatore WASM alternativo
---

## Architettura & flussi

- **Sensor Simulator**  
  Genera `NUM_SPACES` sensori virtuali per un parcheggio (`LOT_ID`) e pubblica su MQTT (topic: `parking/{lotId}/{spaceId}/status`) un JSON con stato occupato/libero. Imposta *Last Will* (LWT) per segnalare sensor offline.

**Aggregator (Python)**  
  Sottoscrive i topic MQTT, mantiene lo stato degli stalli e aggiorna le CRD `ParkingSpace` e `ParkingLot` su Kubernetes.

**Signage (UI)**  
  App FastAPI che mostra lo stato dei parcheggi leggendo le CRD via API Kubernetes.

**Mobile API**  
  Espone REST per elencare i parcheggi e il loro stato, pensata per app mobile.

**WASM Aggregator (SpinKube)**  
  Alternativa all'aggregatore Python: scritto in Rust, gira come WebAssembly tramite SpinKube. Riceve i messaggi MQTT e aggiorna le CRD direttamente via API Kubernetes, con logica simile all'aggregatore Python ma in ambiente WASM. Configurabile tramite `wasm-aggregator/spin.toml`.

Mosquitto espone **8883/TCP** (TLS, mTLS) e **1883/TCP** (non TLS).

---

## CRD (schema sintetico)

### `ParkingSpace` (`parking.smart/v1alpha1`)
```yaml
spec:
  lotId: string
  spaceId: string
status:
  occupied: boolean
  sensorOnline: boolean
  lastSeen: date-time
```

### `ParkingLot` (`parking.smart/v1alpha1`)
```yaml
spec:
  lotId: string
  totalSpaces: integer
status:
  occupied: integer
  free: integer
  lastUpdate: date-time
```

---


## Requisiti

- **Docker**
- **kubectl**
- **k3d**
- **spin**
- **cargo**
- **wasmtime**
- **rustup**
- (Opz.) **helm** per usare lo script `build_deploy_all.cmd` su Windows
- Connessione che consenta il *port-forwarding* verso `localhost`

---

## Avvio rapido (k3d)

> I comandi sono pensati per essere lanciati dalla root del repo `smart-parking-k3d/`.

1) **Cluster + namespace**
```bash
k3d cluster create wasm-cluster ^
    --image ghcr.io/spinframework/containerd-shim-spin/k3d:v0.21.0 ^
    --port "8081:80@loadbalancer" ^
    --agents 2
```

2) **CRD**
```bash
kubectl -n smart-parking apply -f k8s\crds\parkinglot-crd.yaml
kubectl -n smart-parking apply -f k8s\crds\parkingspace-crd.yaml
```

3) **Cert-manager**
```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/%CERTM_VER%/cert-manager.yaml
kubectl wait --for=condition=available --timeout=300s deployment/cert-manager-webhook -n cert-manager
```

4) **Spin Operator (RuntimeClass + CRD + Helm controller)**
```bash
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/%SPIN_OP_VER%/spin-operator.runtime-class.yaml
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/%SPIN_OP_VER%/spin-operator.crds.yaml
helm upgrade --install spin-operator ^
  --namespace spin-operator --create-namespace ^
  --version v0.6.1:~1% ^
  --wait ^
  oci://ghcr.io/spinframework/charts/spin-operator
kubectl -n spin-operator rollout status deploy/spin-operator-controller-manager --timeout=300s
```

5) **Shim Executor**
```bash
kubectl -n smart-parking apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.shim-executor.yaml
kubectl -n smart-parking get spinappexecutors.core.spinkube.dev
```

6) **TLS secrets + (opzionale) RBAC per subscribers**
```bash
kubectl -n smart-parking apply -f k8s\tls\tls-secrets.yaml
kubectl -n smart-parking apply -f k8s\rbac-signage-mobile.yaml
```

7) **Mosquitto (TLS 8883)**
```bash
kubectl -n smart-parking apply -f k8s\mosquitto\configmap.yaml
kubectl -n smart-parking apply -f k8s\mosquitto\deployment.yaml
kubectl -n smart-parking apply -f k8s\mosquitto\service.yaml
kubectl -n smart-parking wait --for=condition=Available deploy/mosquitto --timeout=120s
```

8) **Build immagini applicative**
```bash
docker build -t smart-parking/aggregator:latest        services\aggregator        
docker build -t smart-parking/sensor-simulator:latest  services\sensor-simulator  
docker build -t smart-parking/signage:latest           services\signage           
docker build -t smart-parking/mobile-api:latest        services\mobile-api        
```

9) **Import immagini nel cluster k3d**
```bash
k3d image import ^
  smart-parking/aggregator:latest ^
  smart-parking/sensor-simulator:latest ^
  smart-parking/signage:latest ^
  smart-parking/mobile-api:latest ^
  -c wasm-cluster
```

10) **Deploy applicazioni**
```bash
kubectl -n smart-parking apply -f services\aggregator\deployment.yaml
kubectl -n smart-parking apply -f services\sensor-simulator\deployment.yaml
kubectl -n smart-parking apply -f services\signage\deployment.yaml
kubectl -n smart-parking apply -f services\mobile-api\deployment.yaml
```

11) **Attesa readiness dei deployment**
```bash
kubectl -n smart-parking wait --for=condition=Available deploy/aggregator --timeout=180s
kubectl -n smart-parking wait --for=condition=Available deploy/sensor-simulator --timeout=180s
kubectl -n smart-parking wait --for=condition=Available deploy/signage --timeout=180s
kubectl -n smart-parking wait --for=condition=Available deploy/mobile-api --timeout=180s
```

12) **RBAC + ServiceAccount aggregator Wasm**
```bash
kubectl -n smart-parking apply -f wasm-aggregator\rbac-wasm-aggregator.yaml
```
13) **Token del ServiceAccount + Secret SpinApp**
```bash
kubectl -n smart-parking get sa spinkube-aggregator >nul 2>nul
set "TMP_TOKEN=%TEMP%\k8s.token"
kubectl -n smart-parking create token spinkube-aggregator --duration=24h > "%TMP_TOKEN%"
kubectl -n smart-parking delete secret k8s-token-secret >nul 2>nul
kubectl -n smart-parking create secret generic k8s-token-secret ^
  --from-file=k8s_token="%TMP_TOKEN%"
del /q "%TMP_TOKEN%" >nul 2>nul
```

14) **Deploy SpinApp + Haproxy**
```bash
kubectl -n smart-parking apply -f wasm-aggregator\kubeapi-haproxy.yaml
kubectl -n smart-parking apply -f wasm-aggregator\spinapp.yaml
```

15) **Attesa app Spin**
```bash
kubectl -n smart-parking rollout status deploy/smart-parking-aggregator
```

16) **Accesso rapido via port-forward (HTTPS)**
```bash
kubectl -n smart-parking port-forward svc/signage 8081:443
kubectl -n smart-parking port-forward svc/mobile-api 8082:443
# UI:     https://localhost:8081
# API:    https://localhost:8082/docs
```
---

## MQTT: topic & payload

- **Topic**: `parking/{lotId}/{spaceId}/status`
- **QoS**: 1
- **Birth / Last-Will**: il simulatore imposta LWT che marca `sensorOnline=false` quando cade la connessione.

**Payload esempio** (pubblicazione periodica del simulatore):
```json
{
  "lotId": "A",
  "spaceId": "A-7",
  "sensorId": "sensor-A-A-7-12345",
  "occupied": true,
  "sensorOnline": true,
  "ts": 1726750123
}
```

**LWT** (inviato da broker alla disconnessione del client):
```json
{
  "lotId": "A",
  "spaceId": "A-7",
  "sensorId": "sensor-A-A-7-12345",
  "occupied": null,
  "sensorOnline": false,
  "ts": 1726750123
}
```

---

## API: **Mobile API** (FastAPI)

- `GET /health` → `"ok"`
- `GET /lots` → lista dei parcheggi con:
  ```json
  [{
    "name": "a",
    "lotId": "A",
    "totalSpaces": 10,
    "occupied": 4,
    "free": 6,
    "lastUpdate": "2025-09-21T12:34:56Z"
  }]
  ```

Swagger disponibile su `https://localhost:8082/docs` (via port-forward).

---

## Variabili d'ambiente principali

### Sensor Simulator
- `BROKER_HOST` (default: `mosquitto` in cluster)
- `BROKER_PORT` (default: `8883`)
- `MQTT_TLS` (`1` abilita TLS)
- `MQTT_CA`, `MQTT_CERT`, `MQTT_KEY` (path in container)
- `LOT_ID` (default: `A`)
- `NUM_SPACES` (default: `10`)
- `PUBLISH_INTERVAL` secondi (default: `2.0`)
- `FLAP_PROB` probabilità cambio stato (default: `0.25`)

### Aggregator
- `BROKER_HOST`, `BROKER_PORT`, `MQTT_TLS`, `MQTT_CA`, `MQTT_CERT`, `MQTT_KEY`
- `NAMESPACE` (default: `smart-parking`)
- Usa le API K8s per creare/aggiornare `ParkingSpace`/`ParkingLot` e patchare `.status`

### Signage & Mobile API
- `NAMESPACE` (default: `smart-parking`)
- Accedono in lettura alle CRD (Role/RoleBinding `crd-reader`)

---

## Pulizia

```bash
k3d cluster delete smart-parking
```

---

## WASM Aggregator (SpinKube)

Il componente `wasm-aggregator/` implementa un aggregatore MQTT → CRD in Rust, eseguibile come WebAssembly tramite SpinKube. Permette di gestire i flussi Smart Parking in modo portabile e sicuro, senza dipendenze Python.

- **Codice**: `wasm-aggregator/src/lib.rs`
- **Configurazione**: `wasm-aggregator/spin.toml` (definisce trigger MQTT, variabili ambiente, path CRD, ecc)
- **Build**: `cargo build --target wasm32-wasip1 --release`
- **Deploy**: tramite SpinKube e manifest YAML forniti

Il flusso logico replica quello dell'aggregatore Python: riceve messaggi MQTT, aggiorna/crea le risorse `ParkingLot` e `ParkingSpace` su Kubernetes, effettua il conteggio degli stalli e aggiorna lo stato.

