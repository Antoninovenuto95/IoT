// Modulo principale WASM aggregator per Smart Parking
// Riceve messaggi MQTT e aggiorna le CRD ParkingLot/ParkingSpace su Kubernetes

use spin_sdk::variables;
use serde::{Deserialize, Serialize};
use spin_sdk::http::{self, Request, Response};
// MQTT plugin SDK
use spin_mqtt_sdk::{mqtt_component, Payload, Metadata};

use std::sync::{Once, OnceLock};

// One-time init per loggare la configurazione solo al primo messaggio
static INIT: Once = Once::new();
// Cache globale del token del service account
static TOKEN_CACHE: OnceLock<TokenInfo> = OnceLock::new();

#[derive(Clone)]
struct TokenInfo {
    value: String,       // Il token vero e proprio
    via:   &'static str, // Da dove è stato recuperato
}


// --- Strutture dati per payload e status ---
#[derive(Deserialize)]
struct SpaceMsg {
    occupied: Option<bool>, // Stato occupato/libero dello stallo
    #[serde(default = "default_true")]
    sensor_online: bool,    // Stato online del sensore (default true)
    #[serde(default)]
    ts: Option<i64>,        // Timestamp UNIX (secondi) del messaggio
}

// Funzione di default usata da serde per sensor_online
fn default_true() -> bool { true }

#[derive(Serialize)]
struct ParkingLotStatus {
    occupied: i32,       // Numero stalli occupati
    free: i32,           // Numero stalli liberi
    last_update: String, // Timestamp ultimo aggiornamento (ISO 8601)
}

#[derive(Serialize)]
struct ParkingSpaceStatus {
    occupied: bool,      // Stato occupato dello stallo
    sensor_online: bool, // Stato online del sensore
    last_seen: String,   // Timestamp ultimo messaggio (ISO 8601)
}

// --- Funzioni di utilità per configurazione/env ---
/// Legge una variabile: prima Spin variables, poi env SPIN_VARIABLE_*, poi default.
/// Nota: per "namespace" forziamo l'override a "smart-parking".
fn v(name: &str, default: &str) -> String {
    if name == "namespace" {
        // forza override manuale
        return "smart-parking".to_string();
    }
    if let Ok(val) = spin_sdk::variables::get(name) { return val; }
    let env_key = format!("SPIN_VARIABLE_{}", name.to_ascii_uppercase());
    if let Ok(val) = std::env::var(env_key) { return val; }
    default.to_string()
}

/// Restituisce (scheme, host, port, ns, group, version, token)
/// Aggrega i parametri di connessione verso l'API server K8s.
/// Il token può provenire da più fonti (vedi service_account_token()).
fn env_cfg() -> (String, String, String, String, String, String, Option<String>) {
    let scheme  = v("k8s_scheme", "http");
    // usa sempre FQDN completo per evitare problemi di DNS nei pod
    let host    = v("k8s_host", "kubeapi-proxy.smart-parking.svc.cluster.local");
    let port    = v("k8s_port", "8000");
    let ns      = v("namespace", "smart-parking");
    let group   = v("group", "parking.smart");
    let version = v("version", "v1alpha1");
    let token   = service_account_token().map(|info| info.value.clone());
    (scheme, host, port, ns, group, version, token)
}

/// Recupera e memoizza il token del service account.
/// Ordine: Spin variable -> env SPIN_VARIABLE_K8S_TOKEN -> env K8S_TOKEN -> file SA.
/// Ritorna un riferimento statico in cache se presente.
fn service_account_token() -> Option<&'static TokenInfo> {
    fn clean(value: String) -> Option<String> {
        let trimmed = value.trim().to_string();
        if trimmed.is_empty() { None } else { Some(trimmed) }
    }

    // Se già in cache, restituisci subito
    if let Some(info) = TOKEN_CACHE.get() {
        return Some(info);
    }

    // Risoluzione "una tantum"
    let resolved = || -> Option<TokenInfo> {
        if let Ok(val) = variables::get("k8s_token") {
            if let Some(value) = clean(val) {
                return Some(TokenInfo { value, via: "spin_variables:k8s_token" });
            }
        }

        if let Ok(val) = std::env::var("SPIN_VARIABLE_K8S_TOKEN") {
            if let Some(value) = clean(val) {
                return Some(TokenInfo { value, via: "env:SPIN_VARIABLE_K8S_TOKEN" });
            }
        }

        if let Ok(val) = std::env::var("K8S_TOKEN") {
            if let Some(value) = clean(val) {
                return Some(TokenInfo { value, via: "env:K8S_TOKEN" });
            }
        }

        // Percorso standard montato da Kubernetes nei pod
        const SA_TOKEN_PATH: &str = "/var/run/secrets/kubernetes.io/serviceaccount/token";
        if let Ok(contents) = std::fs::read_to_string(SA_TOKEN_PATH) {
            if let Some(value) = clean(contents) {
                return Some(TokenInfo { value, via: SA_TOKEN_PATH });
            }
        }

        None
    }();

    // Se è stato trovato un token, mettilo in cache per usi futuri
    if let Some(info) = resolved {
        let _ = TOKEN_CACHE.set(info);
        return TOKEN_CACHE.get();
    }

    None
}

/// Costruisce l'header Authorization "Bearer <token>".
/// Fallisce se il token non è disponibile.
fn bearer() -> Result<String, anyhow::Error> {
    if let Some(info) = service_account_token() {
        println!("using token via {} len={}", info.via, info.value.len());
        return Ok(format!("Bearer {}", info.value.as_str()));
    }
    Err(anyhow::anyhow!("missing SA token"))
}

/// Ritorna l'istante corrente in formato RFC3339 (UTC).
fn now_iso() -> String {
    time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap()
}

/// Base URL dell'API server (scheme+host+port).
fn k8s_base() -> String {
    let (scheme, host, port, _ns, _group, _version, _token) = env_cfg();
    format!("{scheme}://{host}:{port}")
}

/// Helpers per leggere singoli parametri (namespace, group, version).
fn ns() -> String {
    let (_scheme, _host, _port, ns, _group, _version, _token) = env_cfg();
    ns
}
fn group() -> String {
    let (_scheme, _host, _port, _ns, group, _version, _token) = env_cfg();
    group
}
fn version() -> String {
    let (_scheme, _host, _port, _ns, _group, version, _token) = env_cfg();
    version
}

// ---------- util ----------
/// True se status HTTP è 2xx.
fn is_success(status: u16) -> bool {
    (200..300).contains(&status)
}

/// Logga il corpo di risposta se lo status non è 2xx (utile per diagnosi).
fn log_non_success(verb: &str, url: &str, resp: &Response) {
    let status = *resp.status();
    if !is_success(status) {
        let body_bytes = resp.body().to_vec();
        let body = String::from_utf8_lossy(&body_bytes);
        eprintln!("{verb} {url} -> {status}; body: {body}");
    }
}

// ---- HTTP helpers (async) ----
/// Esegue GET verso l'API K8s con bearer token e JSON.
async fn k8s_get(path: &str) -> Result<Response, anyhow::Error> {
    let url = format!("{}{}", k8s_base(), path);
    let req = Request::get(&url)
        .header("Authorization", bearer()?)
        .header("Accept", "application/json")
        .build();
    match http::send(req).await {
        Ok(resp) => { log_non_success("GET", &url, &resp); Ok(resp) }
        Err(e) => { eprintln!("GET {url} failed: {e}"); Err(e.into()) }
    }
}

/// Esegue POST verso l'API K8s con corpo e content type forniti.
async fn k8s_post(path: &str, body: Vec<u8>, content_type: &str) -> Result<Response, anyhow::Error> {
    let url = format!("{}{}", k8s_base(), path);
    let req = Request::post(&url, body)
        .header("Authorization", bearer()?)
        .header("Accept", "application/json")
        .header("Content-Type", content_type)
        .build();
    match http::send(req).await {
        Ok(resp) => { log_non_success("POST", &url, &resp); Ok(resp) }
        Err(e) => { eprintln!("POST {url} failed: {e}"); Err(e.into()) }
    }
}

/// Esegue PATCH (merge-patch) verso l'API K8s.
/// Nota: usato sia per /status che per aggiornamenti spec totali.
async fn k8s_patch(path: &str, body: Vec<u8>, content_type: &str) -> Result<Response, anyhow::Error> {
    let url = format!("{}{}", k8s_base(), path);
    let req = Request::patch(&url, body)
        .header("Authorization", bearer()?)
        .header("Accept", "application/json")
        .header("Content-Type", content_type)
        .build();
    match http::send(req).await {
        Ok(resp) => { log_non_success("PATCH", &url, &resp); Ok(resp) }
        Err(e) => { eprintln!("PATCH {url} failed: {e}"); Err(e.into()) }
    }
}

// ---- CRD helpers ----
/// Crea ParkingLot se non esiste; se esiste e viene passato total_spaces, aggiorna lo spec.totalSpaces.
async fn ensure_parkinglot(lot_id: &str, total_spaces: Option<i32>) {
    let name = lot_id.to_lowercase();
    let body = serde_json::json!({
        "apiVersion": format!("{}/{}", group(), version()),
        "kind": "ParkingLot",
        "metadata": { "name": name },
        "spec": { "lotId": lot_id, "totalSpaces": total_spaces.unwrap_or(0) }
    });
    let path = format!("/apis/{}/{}/namespaces/{}/parkinglots", group(), version(), ns());
    if let Ok(r) = k8s_post(&path, serde_json::to_vec(&body).unwrap(), "application/json").await {
        let status = *r.status();
        if status == 201 {
            println!("Created ParkingLot/{name}");
        } else if status == 409 {
            // Esiste già: se viene fornito total_spaces, facciamo un merge-patch dello spec
            if let Some(ts) = total_spaces {
                let patch = serde_json::json!({ "spec": { "totalSpaces": ts }});
                let p2 = format!("/apis/{}/{}/namespaces/{}/parkinglots/{}", group(), version(), ns(), name);
                let _ = k8s_patch(&p2, serde_json::to_vec(&patch).unwrap(), "application/merge-patch+json").await;
                println!("Ensured ParkingLot/{name} totalSpaces={ts}");
            }
        }
    }
}

/// Aggiorna lo status del ParkingLot (occupied/free/last_update).
/// Se la risorsa non esiste (404), la crea e ritenta il patch.
async fn patch_parkinglot_status(lot_id: &str, occupied: i32, free: i32) {
    let name = lot_id.to_lowercase();
    let status = ParkingLotStatus { occupied, free, last_update: now_iso() };
    let body = serde_json::json!({ "status": status });
    let path = format!("/apis/{}/{}/namespaces/{}/parkinglots/{}/status", group(), version(), ns(), name);
    if let Ok(r) = k8s_patch(&path, serde_json::to_vec(&body).unwrap(), "application/merge-patch+json").await {
        let sc = *r.status();
        if sc == 404 {
            println!("ParkingLot/{name} not found; creating then patching status");
            ensure_parkinglot(lot_id, Some(occupied + free)).await;
            let _ = k8s_patch(&path, serde_json::to_vec(&body).unwrap(), "application/merge-patch+json").await;
        } else if is_success(sc) {
            println!("Patched ParkingLot/{name} status -> occupied={occupied}, free={free}");
        }
    }
}

/// Crea ParkingSpace se non esiste (id composto lot-space minuscolo).
async fn ensure_parkingspace(lot_id: &str, space_id: &str) {
    let name = format!("{}-{}", lot_id, space_id).to_lowercase();
    let body = serde_json::json!({
        "apiVersion": format!("{}/{}", group(), version()),
        "kind": "ParkingSpace",
        "metadata": { "name": name },
        "spec": { "lotId": lot_id, "spaceId": space_id }
    });
    let path = format!("/apis/{}/{}/namespaces/{}/parkingspaces", group(), version(), ns());
    let _ = k8s_post(&path, serde_json::to_vec(&body).unwrap(), "application/json").await;
}

/// Aggiorna lo status del ParkingSpace; se non esiste, lo crea e riprova.
/// last_seen_iso deve essere già in formato ISO 8601.
async fn patch_parkingspace_status(lot_id: &str, space_id: &str, occupied: bool, sensor_online: bool, last_seen_iso: String) {
    let name = format!("{}-{}", lot_id, space_id).to_lowercase();
    let status = ParkingSpaceStatus { occupied, sensor_online, last_seen: last_seen_iso };
    let body = serde_json::json!({ "status": status });
    let path = format!("/apis/{}/{}/namespaces/{}/parkingspaces/{}/status", group(), version(), ns(), name);
    if let Ok(r) = k8s_patch(&path, serde_json::to_vec(&body).unwrap(), "application/merge-patch+json").await {
        let sc = *r.status();
        if sc == 404 {
            println!("ParkingSpace/{name} not found; creating then patching status");
            ensure_parkingspace(lot_id, space_id).await;
            let _ = k8s_patch(&path, serde_json::to_vec(&body).unwrap(), "application/merge-patch+json").await;
        } else if is_success(sc) {
            println!("Patched ParkingSpace/{name} status -> occupied={occupied}, online={sensor_online}");
        }
    }
}

// --- recount_lot ---
/// Riconta gli stalli di un dato lot interrogando tutte le ParkingSpace nel namespace.
/// Ritorna (occupied, free). In caso di errore restituisce (0, 0).
async fn recount_lot(lot_id: &str) -> (i32, i32) {
    // Nota: per semplicità lista tutte le parkingspaces nel ns e filtra lato client.
    let path = format!("/apis/{}/{}/namespaces/{}/parkingspaces", group(), version(), ns());
    let mut occupied = 0i32;
    let mut total = 0i32;

    match k8s_get(&path).await {
        Ok(resp) => {
            let status = *resp.status();
            let bytes = resp.body().to_vec();
            let preview = String::from_utf8_lossy(&bytes);
            println!("GET {} -> {}", path, status);
            println!("GET body (preview): {}", &preview.chars().take(300).collect::<String>());

            if !is_success(status) {
                return (0, 0);
            }

            // Parsing del JSON restituito dalla LIST delle CRD
            match serde_json::from_slice::<serde_json::Value>(&bytes) {
                Ok(doc) => {
                    if let Some(items) = doc.get("items").and_then(|v| v.as_array()) {
                        for it in items {
                            let spec_lot = it.get("spec")
                                .and_then(|s| s.get("lotId"))
                                .and_then(|v| v.as_str());

                            // Filtra solo le spaces appartenenti al lot richiesto (case-insensitive)
                            if spec_lot.map(|s| s.eq_ignore_ascii_case(lot_id)).unwrap_or(false) {
                                total += 1;
                                let occ = it.get("status")
                                    .and_then(|s| s.get("occupied"))
                                    .and_then(|v| v.as_bool())
                                    .unwrap_or(false);
                                if occ { occupied += 1; }
                            }
                        }
                    } else {
                        eprintln!("JSON ok ma 'items' assente o non array");
                    }
                }
                Err(e) => {
                    eprintln!("Errore parsing JSON parkingspaces: {e}");
                }
            }
        }
        Err(e) => {
            eprintln!("GET {} failed: {}", path, e);
            return (0, 0);
        }
    }

    let free = (total - occupied).max(0);
    println!("Recount lot {lot_id} -> occupied={occupied}, free={free}, total={total}");
    (occupied, free)
}

// --- Handler principale per i messaggi MQTT ---
/// Entrypoint invocato dal runtime Spin per ogni messaggio MQTT.
/// Atteso topic: parking/{lot}/{space}/status
#[mqtt_component]
async fn on_mqtt_message(message: Payload, _meta: Metadata) -> anyhow::Result<()> {
    let topic = _meta.topic.clone();
    println!("MQTT message on topic: {}", topic);

    // Log di configurazione SOLO al primo messaggio (thread-safe)
    INIT.call_once(|| {
        let (scheme, host, port, ns, group, version, tok) = env_cfg();
        println!(
            "cfg scheme={scheme} host={host} port={port} ns={ns} group={group} version={version} token_present={}",
            tok.is_some()
        );
    });

    // Parsing del topic e del payload
    let parts: Vec<&str> = topic.split('/').collect();
    if parts.len() >= 4 && parts[0] == "parking" && parts[3] == "status" {
        let lot_id = parts[1];
        let space_id = parts[2];

        // Decodifica JSON del payload con fallback robusto sugli opzionali
        let data: SpaceMsg = match serde_json::from_slice(&message) {
            Ok(d) => d,
            Err(e) => {
                eprintln!("Invalid payload on {topic}: {e}");
                return Ok(());
            }
        };

        // occupied default false se assente
        let occupied = data.occupied.unwrap_or(false);
        let sensor_online = data.sensor_online;

        // Usa ts se presente, altrimenti now(); conversione in OffsetDateTime
        let ts = data.ts.unwrap_or_else(|| time::OffsetDateTime::now_utc().unix_timestamp());
        let last_seen_iso = time::OffsetDateTime::from_unix_timestamp(ts)
            .unwrap_or(time::OffsetDateTime::now_utc())
            .format(&time::format_description::well_known::Rfc3339)
            .unwrap();

        println!(
            "Upserting lot={lot_id}, space={space_id}, occupied={occupied}, online={sensor_online}"
        );

        // Crea/aggiorna le risorse su K8s (idempotenti)
        ensure_parkinglot(lot_id, None).await;
        ensure_parkingspace(lot_id, space_id).await;
        patch_parkingspace_status(lot_id, space_id, occupied, sensor_online, last_seen_iso).await;

        // Riconta gli stalli del parcheggio e aggiorna lo stato aggregato
        println!("Recounting lot {} ...", lot_id);
        let (occ, free) = recount_lot(lot_id).await;
        println!("Recounted lot {} -> occupied={}, free={}, total={}", lot_id, occ, free, occ + free);

        // Mantiene spec.totalSpaces allineato e patcha lo status complessivo
        ensure_parkinglot(lot_id, Some(occ + free)).await;
        patch_parkinglot_status(lot_id, occ, free).await;

        println!("Done lot={lot_id}, space={space_id}");
    } else {
        // Topic non riconosciuto: ignora ma logga per diagnosi
        println!("Topic does not match parking/{{lot}}/{{space}}/status: {topic}");
    }
    Ok(())
}
