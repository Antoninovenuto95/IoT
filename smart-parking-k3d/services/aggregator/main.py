# Aggregatore per Smart Parking
# Riceve i messaggi MQTT dai sensori e aggiorna le CRD Kubernetes ParkingSpace/ParkingLot
import os
import json
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from kube import load_kube_config_safely

# === Configurazione ===
BROKER_HOST = os.getenv("BROKER_HOST", "mosquitto")  # Host del broker MQTT
BROKER_PORT = int(os.getenv("BROKER_PORT", "8883")) # Porta MQTT

# TLS/mTLS
MQTT_TLS  = os.getenv("MQTT_TLS", "0") == "1"       # Abilita TLS/mTLS se = "1"
MQTT_CA   = os.getenv("MQTT_CA", "/etc/mqtt/ca.crt")
MQTT_CERT = os.getenv("MQTT_CERT", "/etc/mqtt/client.crt")
MQTT_KEY  = os.getenv("MQTT_KEY", "/etc/mqtt/client.key")

# CRD
NAMESPACE = os.getenv("NAMESPACE", os.getenv("POD_NAMESPACE", "smart-parking")) # Namespace K8s
GROUP = "parking.smart"    # Gruppo CRD
VERSION = "v1alpha1"      # Versione CRD

# === Inizializza client Kubernetes ===
load_kube_config_safely()
crd = k8s_client.CustomObjectsApi()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# ---------- CRD helpers ----------
# Funzioni helper per creare/patchare ParkingLot
# ensure_parkinglot: crea la risorsa ParkingLot se non esiste, aggiorna totalSpaces se necessario
# upsert_parkinglot_status: aggiorna lo stato (occupied, free, lastUpdate) della risorsa ParkingLot
def ensure_parkinglot(lot_id: str, total_spaces: int | None = None):
    name = lot_id.lower()
    body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "ParkingLot",
        "metadata": {"name": name},
        "spec": {"lotId": lot_id, "totalSpaces": int(total_spaces or 0)},
    }
    try:
        try:
            crd.create_namespaced_custom_object(GROUP, VERSION, NAMESPACE, "parkinglots", body)
            print(f"[CRD] ParkingLot creato: {name}")
        except ApiException as e:
            if e.status == 409:
                if total_spaces is not None:
                    try:
                        cur = crd.get_namespaced_custom_object(GROUP, VERSION, NAMESPACE, "parkinglots", name)
                        cur_spec = cur.get("spec", {})
                        cur_total = int(cur_spec.get("totalSpaces", 0))
                        if total_spaces > cur_total:
                            patch = {"spec": {"totalSpaces": int(total_spaces)}}
                            crd.patch_namespaced_custom_object(GROUP, VERSION, NAMESPACE, "parkinglots", name, patch)
                    except Exception as e2:
                        print("[CRD] errore nel patchare lot spec.totalSpaces:", e2)
            else:
                raise
    except Exception as e:
        print("[CRD] errore in ensure_parkinglot:", e)

def upsert_parkinglot_status(lot_id: str, occupied: int, free: int):
    name = lot_id.lower()
    status = {"occupied": int(occupied), "free": int(free), "lastUpdate": now_iso()}
    try:
        crd.patch_namespaced_custom_object_status(
            GROUP, VERSION, NAMESPACE, "parkinglots", name, {"status": status}
        )
    except ApiException as e:
        if e.status == 404:
            ensure_parkinglot(lot_id, occupied + free)
            try:
                crd.patch_namespaced_custom_object_status(
                    GROUP, VERSION, NAMESPACE, "parkinglots", name, {"status": status}
                )
            except Exception as e2:
                print("[CRD] errore nel patchare lo stato del lot dopo la creazione:", e2)
        else:
            print("[CRD] errore nel patchare lo stato del lot:", e)

# Funzioni helper per creare/patchare ParkingSpace
# ensure_parkingspace: crea la risorsa ParkingSpace se non esiste
# upsert_parkingspace_status: aggiorna lo stato (occupied, sensorOnline, lastSeen) della risorsa ParkingSpace
def ensure_parkingspace(lot_id: str, space_id: str):
    name = f"{lot_id}-{space_id}".lower()
    body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "ParkingSpace",
        "metadata": {"name": name},
        "spec": {"lotId": lot_id, "spaceId": space_id},
    }
    try:
        try:
            crd.create_namespaced_custom_object(GROUP, VERSION, NAMESPACE, "parkingspaces", body)
            print(f"[CRD] ParkingSpace creato: {name}")
        except ApiException as e:
            if e.status != 409:
                raise
    except Exception as e:
        print("[CRD] errore in ensure_parkingspace:", e)

def upsert_parkingspace_status(lot_id: str, space_id: str, occupied: bool, sensor_online: bool, last_seen_iso: str):
    name = f"{lot_id}-{space_id}".lower()
    status = {"occupied": bool(occupied), "sensorOnline": bool(sensor_online), "lastSeen": last_seen_iso}
    try:
        crd.patch_namespaced_custom_object_status(
            GROUP, VERSION, NAMESPACE, "parkingspaces", name, {"status": status}
        )
    except ApiException as e:
        if e.status == 404:
            ensure_parkingspace(lot_id, space_id)
            try:
                crd.patch_namespaced_custom_object_status(
                    GROUP, VERSION, NAMESPACE, "parkingspaces", name, {"status": status}
                )
            except Exception as e2:
                print("[CRD] errore nel patchare lo stato dello spazio dopo la creazione:", e2)
        else:
            print("[CRD] errore nel patchare lo stato dello spazio:", e)

# ---------- Stato per-lot ----------
lot_state: dict[str, dict[str, bool]] = {}

# Funzione per ricalcolare e pubblicare lo stato di un ParkingLot
def recompute_and_publish_lot(lot_id: str):
    spaces = lot_state.get(lot_id, {})
    total = len(spaces)
    occupied = sum(1 for v in spaces.values() if v)
    free = max(0, total - occupied)

    ensure_parkinglot(lot_id, total)
    upsert_parkinglot_status(lot_id, occupied, free)

# ---------- Callback MQTT ----------
def on_connect(client: mqtt.Client, _userdata, _flags, rc: int):
    print(f"[MQTT] connesso rc={rc}")
    client.subscribe("parking/+/+/status", qos=1)
    print("[MQTT] iscritto a parking/+/+/status")

def on_message(_client: mqtt.Client, _userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        print("[MQTT] payload non-JSON su", msg.topic)
        return

    parts = msg.topic.split("/")
    if len(parts) >= 4 and parts[0] == "parking" and parts[3] == "status":
        lot_id, space_id = parts[1], parts[2]
        occupied = bool(data.get("occupied"))
        sensor_online = bool(data.get("sensorOnline", True))
        ts = int(data.get("ts", time.time()))
        last_seen_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        ensure_parkinglot(lot_id)
        ensure_parkingspace(lot_id, space_id)
        upsert_parkingspace_status(lot_id, space_id, occupied, sensor_online, last_seen_iso)

        lot = lot_state.setdefault(lot_id, {})
        lot[space_id] = occupied
        recompute_and_publish_lot(lot_id)

# ---------- Main ----------
def main():
    client = mqtt.Client(client_id="aggregator")
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=5)

    if MQTT_TLS:
        client.tls_set(ca_certs=MQTT_CA, certfile=MQTT_CERT, keyfile=MQTT_KEY)
        client.tls_insecure_set(False)

    client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=30)
    print(f"[MQTT] connessione a {BROKER_HOST}:{BROKER_PORT} ...")
    client.loop_forever()


if __name__ == "__main__":
    main()
