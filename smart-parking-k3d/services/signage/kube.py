# Helper per la configurazione del client Kubernetes

import os
from kubernetes import config

# Carica la configurazione Kubernetes
# Usa la config in-cluster se disponibile, altrimenti il file kubeconfig locale
def load_kube_config_safely():
    try:
        config.load_incluster_config()
    except Exception:
        kubeconfig = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))
        config.load_kube_config(config_file=kubeconfig)
