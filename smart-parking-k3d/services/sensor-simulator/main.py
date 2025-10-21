# Simulatore di sensori per Smart Parking
# Questo script crea NUM_SPACES sensori virtuali che pubblicano lo stato su MQTT
import os
import time
import json
import random
import threading
import paho.mqtt.client as mqtt

# --- Configurazione tramite variabili d'ambiente ---
BROKER_HOST = os.getenv("BROKER_HOST", "mosquitto")  # Host del broker MQTT (default: mosquitto nel cluster)
BROKER_PORT = int(os.getenv("BROKER_PORT", "8883"))  # Porta MQTT (default: 8883 TLS)
MQTT_TLS = os.getenv("MQTT_TLS", "0") == "1"         # Abilita TLS/mTLS se = "1"
MQTT_CA = os.getenv("MQTT_CA", "/etc/mqtt/ca.crt")   # Path CA
MQTT_CERT = os.getenv("MQTT_CERT", "/etc/mqtt/client.crt") # Path cert client
MQTT_KEY = os.getenv("MQTT_KEY", "/etc/mqtt/client.key")   # Path chiave client
LOT_ID = os.getenv("LOT_ID", "A")                    # ID parcheggio
NUM_SPACES = int(os.getenv("NUM_SPACES", "10"))      # Numero stalli simulati
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "2.0")) # Intervallo pubblicazione
FLAP_PROB = float(os.getenv("FLAP_PROB", "0.25"))   # Probabilità cambio stato

# Funzione per creare e avviare un sensore virtuale
# Ogni sensore pubblica su un topic dedicato e gestisce LWT (Last Will)
def make_sensor(space_id: str):
    client_id = f"sensor-{LOT_ID}-{space_id}-{os.getpid()}-{random.randint(1000,9999)}"
    topic = f"parking/{LOT_ID}/{space_id}/status"
    c = mqtt.Client(client_id=client_id, clean_session=True)
    c.reconnect_delay_set(min_delay=1, max_delay=5)

    # Imposta Last Will (LWT): messaggio inviato dal broker se il sensore si disconnette
    lwt = {"occupied": None, "sensorOnline": False, "ts": int(time.time()),
           "lotId": LOT_ID, "spaceId": space_id, "sensorId": client_id}
    c.will_set(topic, payload=json.dumps(lwt), qos=1, retain=True)

    # Callback su connessione
    def on_connect(client, _userdata, _flags, rc):
        print(f"[{space_id}] connected rc={rc}")
        birth = {"occupied": False, "sensorOnline": True, "ts": int(time.time()),
                 "lotId": LOT_ID, "spaceId": space_id, "sensorId": client_id}
        client.publish(topic, json.dumps(birth), qos=1, retain=True)
        print(f"[{space_id}] birth sent")

    # Callback su disconnessione
    def on_disconnect(_client, _userdata, rc):
        print(f"[{space_id}] disconnected rc={rc}")

    c.on_connect = on_connect
    c.on_disconnect = on_disconnect

    # Configurazione TLS/mTLS se abilitata
    if MQTT_TLS:
        c.tls_set(ca_certs=MQTT_CA, certfile=MQTT_CERT, keyfile=MQTT_KEY)
        c.tls_insecure_set(False)

    c.connect_async(BROKER_HOST, BROKER_PORT, keepalive=30)
    c.loop_start()

    occupied = False

    # Loop di pubblicazione periodica dello stato
    def loop():
        nonlocal occupied
        while True:
            if random.random() < FLAP_PROB:
                occupied = not occupied
            payload = {
                "occupied": occupied, "sensorOnline": True, "ts": int(time.time()),
                "lotId": LOT_ID, "spaceId": space_id, "sensorId": client_id
            }
            r = c.publish(topic, payload=json.dumps(payload), qos=1, retain=True)
            if r.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"[{space_id}] publish failed rc={r.rc}")
            else:
                print(f"[{space_id}] sent occupied={occupied}")
            time.sleep(PUBLISH_INTERVAL + random.uniform(0, 1.0))

    t = threading.Thread(target=loop, daemon=True, name=f"sensor-{space_id}")
    t.start()
    return t

# Funzione principale: avvia tutti i sensori e mantiene il processo attivo
def main():
    spaces = [f"{LOT_ID}-{i+1}" for i in range(NUM_SPACES)]
    threads = [make_sensor(s) for s in spaces]
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
