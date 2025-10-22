@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  Smart Parking - Setup Unificato (TLS + SpinKube)
REM  Requisiti: Docker Desktop, kubectl, k3d, helm, spin, cargo, wasmtime, rustup
REM ============================================================

REM ===== Settings =====
set "CLUSTER=wasm-cluster"
set "NS=smart-parking"
set "SPIN_OP_VER=v0.6.1"
set "CERTM_VER=v1.14.3"

echo.
echo [0/15] Prerequisiti: docker, kubectl, k3d, helm, spin, cargo, wasmtime, rustup
where docker >nul 2>nul || (echo ERRORE: docker non trovato & exit /b 1)
where kubectl >nul 2>nul || (echo ERRORE: kubectl non trovato & exit /b 1)
where k3d >nul 2>nul || (echo ERRORE: k3d non trovato & exit /b 1)
where helm >nul 2>nul || (echo ERRORE: helm non trovato & exit /b 1)
where spin >nul 2>nul || (echo ERRORE: helm non trovato & exit /b 1)
where cargo >nul 2>nul || (echo ERRORE: helm non trovato & exit /b 1)
where wasmtime >nul 2>nul || (echo ERRORE: helm non trovato & exit /b 1)
where rustup >nul 2>nul || (echo ERRORE: helm non trovato & exit /b 1)

echo.
echo [1/15] Creazione del cluster k3d con containerd-shim-spin
set "FOUND="
for /f "tokens=1 delims= " %%a in ('k3d cluster list ^| findstr /i "^%CLUSTER% " 2^>nul') do set FOUND=1
if not defined FOUND (
  k3d cluster create %CLUSTER% ^
    --image ghcr.io/spinframework/containerd-shim-spin/k3d:v0.21.0 ^
    --port "8081:80@loadbalancer" ^
    --agents 2 || goto :err
) else (
  echo   Cluster gia' presente, ok.
)

echo.
echo [2/15] Namespace app + CRD di progetto
kubectl get ns %NS% >nul 2>nul || kubectl create namespace %NS% || goto :err
if not exist "k8s\crds\parkinglot-crd.yaml"   (echo ERRORE: manca k8s\crds\parkinglot-crd.yaml & goto :err)
if not exist "k8s\crds\parkingspace-crd.yaml" (echo ERRORE: manca k8s\crds\parkingspace-crd.yaml & goto :err)
REM Le CRD sono cluster-scoped: l'opzione -n e' innocua ma non necessaria
kubectl apply -f k8s\crds\parkinglot-crd.yaml || goto :err
kubectl apply -f k8s\crds\parkingspace-crd.yaml || goto :err

echo.
echo [3/15] Installazione cert-manager (%CERTM_VER%)
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/%CERTM_VER%/cert-manager.yaml || goto :err
kubectl wait --for=condition=available --timeout=300s deployment/cert-manager-webhook -n cert-manager || goto :err

echo.
echo [4/15] Spin Operator (RuntimeClass + CRD + Helm controller)
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/%SPIN_OP_VER%/spin-operator.runtime-class.yaml || goto :err
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/%SPIN_OP_VER%/spin-operator.crds.yaml || goto :err
helm upgrade --install spin-operator ^
  --namespace spin-operator --create-namespace ^
  --version %SPIN_OP_VER:~1% ^
  --wait ^
  oci://ghcr.io/spinframework/charts/spin-operator || goto :err
kubectl -n spin-operator rollout status deploy/spin-operator-controller-manager --timeout=300s || goto :err

echo.
echo [5/15] Creazione Shim Executor (namespace %NS%)
kubectl -n %NS% apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.shim-executor.yaml || goto :err
kubectl -n %NS% get spinappexecutors.core.spinkube.dev

echo.
echo [6/15] TLS secrets + (opzionale) RBAC per subscribers
kubectl -n %NS% apply -f k8s\tls\tls-secrets.yaml || goto :err
if exist k8s\rbac-signage-mobile.yaml (
  kubectl -n %NS% apply -f k8s\rbac-signage-mobile.yaml || goto :err
) else (
  echo   Avviso: k8s\rbac-signage-mobile.yaml non trovato. Continuo...
)

echo.
echo [7/15] Mosquitto (TLS 8883)
kubectl -n %NS% apply -f k8s\mosquitto\configmap.yaml || goto :err
kubectl -n %NS% apply -f k8s\mosquitto\deployment.yaml || goto :err
kubectl -n %NS% apply -f k8s\mosquitto\service.yaml || goto :err
kubectl -n %NS% wait --for=condition=Available deploy/mosquitto --timeout=120s || goto :err

echo.
echo [8/15] Build immagini applicative
docker build -t smart-parking/aggregator:latest        services\aggregator        || goto :err
docker build -t smart-parking/sensor-simulator:latest  services\sensor-simulator  || goto :err
docker build -t smart-parking/signage:latest           services\signage           || goto :err
docker build -t smart-parking/mobile-api:latest        services\mobile-api        || goto :err

echo.
echo [9/15] Import immagini nel cluster k3d
k3d image import ^
  smart-parking/aggregator:latest ^
  smart-parking/sensor-simulator:latest ^
  smart-parking/signage:latest ^
  smart-parking/mobile-api:latest ^
  -c %CLUSTER% || goto :err

echo.
echo [10/15] Deploy applicazioni
kubectl -n %NS% apply -f services\aggregator\deployment.yaml || goto :err
kubectl -n %NS% apply -f services\sensor-simulator\deployment.yaml || goto :err
kubectl -n %NS% apply -f services\signage\deployment.yaml || goto :err
kubectl -n %NS% apply -f services\mobile-api\deployment.yaml || goto :err

echo.
echo [11/15] Attesa readiness dei deployment
kubectl -n %NS% wait --for=condition=Available deploy/aggregator --timeout=180s || goto :err
kubectl -n %NS% wait --for=condition=Available deploy/sensor-simulator --timeout=180s || goto :err
kubectl -n %NS% wait --for=condition=Available deploy/signage --timeout=180s || goto :err
kubectl -n %NS% wait --for=condition=Available deploy/mobile-api --timeout=180s || goto :err

echo.
echo [12/15] RBAC + ServiceAccount aggregator Wasm
if not exist "wasm-aggregator\rbac-wasm-aggregator.yaml" (
  echo ERRORE: manca rbac-wasm-aggregator.yaml & goto :err
)
kubectl -n %NS% apply -f wasm-aggregator\rbac-wasm-aggregator.yaml || goto :err

echo.
echo [13/15] Token del ServiceAccount + Secret SpinApp
REM Verifica dell'esistenza del SA
kubectl -n %NS% get sa spinkube-aggregator >nul 2>nul || (
  echo ERRORE: ServiceAccount spinkube-aggregator non trovato nel namespace %NS%.
  echo Assicurati che wasm-aggregator\rbac-wasm-aggregator.yaml sia stato applicato.
  goto :err
)

REM Rigenerazione del token (validita' 24h) e creazione Secret idempotentemente
set "TMP_TOKEN=%TEMP%\k8s.token"
kubectl -n %NS% create token spinkube-aggregator --duration=24h > "%TMP_TOKEN%" || goto :err

REM Se il Secret esiste gia', lo elimino per evitare "AlreadyExists"
kubectl -n %NS% delete secret k8s-token-secret >nul 2>nul

kubectl -n %NS% create secret generic k8s-token-secret ^
  --from-file=k8s_token="%TMP_TOKEN%" || goto :err

del /q "%TMP_TOKEN%" >nul 2>nul

echo.
echo [14/15] Deploy SpinApp + HAproxy
if not exist "wasm-aggregator\spinapp.yaml" (
  echo ERRORE: manca spinapp.yaml & goto :err
)
kubectl -n %NS% apply -f wasm-aggregator\kubeapi-haproxy.yaml || goto :err
kubectl -n %NS% apply -f wasm-aggregator\spinapp.yaml || goto :err

echo.
echo [15/15] Attesa avvio app Spin
kubectl -n %NS% get spinapp
REM Il controller crea un Deployment con nome = metadata.name della SpinApp (se l'executor crea i Deployment)
kubectl -n %NS% rollout status deploy/smart-parking-aggregator --timeout=300s || (
  echo   Cerco Pod della SpinApp...
  for /f "skip=1 tokens=1" %%P in ('kubectl -n %NS% get pods -l app=smart-parking-aggregator -o name 2^>nul') do kubectl -n %NS% wait --for=condition=Ready %%P --timeout=300s
)

echo.
echo ==============================
echo  Deploy completato con successo!
echo  Avvio port-forward in nuove finestre CMD...
echo ==============================
start cmd /k "kubectl -n %NS% port-forward svc/signage 8081:443"
start cmd /k "kubectl -n %NS% port-forward svc/mobile-api 8082:443"

echo.
echo Apri:
echo   Segnaletica UI: https://localhost:8081
echo   Mobile API Swagger: https://localhost:8082/docs
exit /b 0

:err
echo.
echo *** ERRORE durante build/deploy. Vedi messaggi sopra. ***
exit /b 1
