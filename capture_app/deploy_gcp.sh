#!/usr/bin/env bash
# Provision a GCP Always-Free e2-micro and run the capture app on it.
#
# RUN THIS YOURSELF. It creates billable-capable resources under your account.
#
#   bash capture_app/deploy_gcp.sh create     # make the VM and start capture
#   CAPTURE_GCS_BUCKET=globally-unique-name bash capture_app/deploy_gcp.sh bucket-create
#   bash capture_app/deploy_gcp.sh status     # remote --status
#   bash capture_app/deploy_gcp.sh logs       # tail the service
#   bash capture_app/deploy_gcp.sh destroy    # delete the VM
#
# FREE-TIER CONSTRAINTS THAT ARE EASY TO GET WRONG. Google's Always Free e2-micro is free ONLY
# with all of these true. Miss one and it silently becomes a billed instance:
#   - machine type   e2-micro   (e2-small is NOT free)
#   - region         us-west1 | us-central1 | us-east1   (any other region is billed)
#   - disk           <= 30 GB standard persistent (pd-standard). SSD is NOT free.
#   - one instance   per month across those regions
#   - egress         free tier excludes China/Australia; 1 GB/month elsewhere
# Market data is inbound, which is not charged. Uploading archives to GCS in the SAME region is
# also free; uploading to another cloud is egress and will cost.
set -euo pipefail

NAME="${CAPTURE_VM_NAME:-btc-capture}"
ZONE="${CAPTURE_ZONE:-us-central1-a}"
MACHINE="${CAPTURE_MACHINE_TYPE:-e2-micro}"
DISK_GB="${CAPTURE_DISK_GB:-30}"
IMAGE_FAMILY="debian-12"
IMAGE_PROJECT="debian-cloud"
GCS_BUCKET="${CAPTURE_GCS_BUCKET:-}"
GCS_PREFIX="${CAPTURE_GCS_PREFIX:-btc-capture}"
GCS_LOCATION="${CAPTURE_GCS_LOCATION:-${ZONE%-*}}"
COLDLINE_AFTER_DAYS="${CAPTURE_COLDLINE_AFTER_DAYS:-90}"
ARCHIVE_AFTER_DAYS="${CAPTURE_ARCHIVE_AFTER_DAYS:-365}"
PYTH_KEY="${PYTH_API_KEY:-}"
PYTH_ENDPOINT_VALUE="${PYTH_ENDPOINT:-}"

resolve_gcloud() {
  if [[ -n "${GCLOUD_COMMAND:-}" && -x "${GCLOUD_COMMAND}" ]]; then
    printf '%s\n' "${GCLOUD_COMMAND}"
    return
  fi
  if command -v gcloud >/dev/null 2>&1; then
    command -v gcloud
    return
  fi
  if command -v gcloud.cmd >/dev/null 2>&1; then
    command -v gcloud.cmd
    return
  fi
  if command -v cygpath >/dev/null 2>&1 && [[ -n "${LOCALAPPDATA:-}" ]]; then
    candidate="$(cygpath -u "${LOCALAPPDATA}")/Google/Cloud SDK/google-cloud-sdk/bin/gcloud"
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  fi
  echo "REFUSING: gcloud was not found. Set GCLOUD_COMMAND to its executable path." >&2
  exit 1
}

GCLOUD="$(resolve_gcloud)"
gc() { "${GCLOUD}" "$@"; }

selected_project() {
  local project
  project="$(gc config get-value project 2>/dev/null)"
  if [[ -z "${project}" || "${project}" == "(unset)" ]]; then
    echo "REFUSING: choose a GCP project with 'gcloud config set project PROJECT_ID'." >&2
    exit 1
  fi
  printf '%s\n' "${project}"
}

require_billing() {
  local project="$1" enabled
  enabled="$(gc billing projects describe "${project}" --format='value(billingEnabled)' 2>/dev/null || true)"
  if [[ "${enabled,,}" != "true" ]]; then
    echo "REFUSING: billing is not enabled for project ${project}." >&2
    echo "Enable billing deliberately before creating the VM or GCS bucket." >&2
    exit 1
  fi
}

validate_days() {
  local name="$1" value="$2"
  case "${value}" in
    ''|*[!0-9]*) echo "REFUSING: ${name} must be a positive integer."; exit 1 ;;
  esac
  if (( value < 1 )); then
    echo "REFUSING: ${name} must be a positive integer."
    exit 1
  fi
}

bucket_create() {
  if [[ -z "${GCS_BUCKET}" ]]; then
    echo "REFUSING: set CAPTURE_GCS_BUCKET to a globally unique bucket name."
    exit 1
  fi
  validate_days CAPTURE_COLDLINE_AFTER_DAYS "${COLDLINE_AFTER_DAYS}"
  validate_days CAPTURE_ARCHIVE_AFTER_DAYS "${ARCHIVE_AFTER_DAYS}"
  if (( ARCHIVE_AFTER_DAYS <= COLDLINE_AFTER_DAYS )); then
    echo "REFUSING: archive transition must be later than coldline transition."
    exit 1
  fi
  project="$(selected_project)"
  require_billing "${project}"
  lifecycle="$(mktemp)"
  trap 'rm -f "${lifecycle:-}"' EXIT
  cat >"${lifecycle}" <<JSON
{
  "rule": [
    {
      "action": {"type": "SetStorageClass", "storageClass": "COLDLINE"},
      "condition": {"age": ${COLDLINE_AFTER_DAYS}, "matchesStorageClass": ["STANDARD"]}
    },
    {
      "action": {"type": "SetStorageClass", "storageClass": "ARCHIVE"},
      "condition": {"age": ${ARCHIVE_AFTER_DAYS}, "matchesStorageClass": ["STANDARD", "NEARLINE", "COLDLINE"]}
    }
  ]
}
JSON
  if gc storage buckets describe "gs://${GCS_BUCKET}" >/dev/null 2>&1; then
    echo "bucket gs://${GCS_BUCKET} already exists; updating guarded settings"
    gc storage buckets update "gs://${GCS_BUCKET}" \
      --uniform-bucket-level-access --public-access-prevention \
      --lifecycle-file="${lifecycle}"
  else
    echo "creating private regional bucket gs://${GCS_BUCKET} in ${GCS_LOCATION}"
    gc storage buckets create "gs://${GCS_BUCKET}" \
      --project="${project}" --location="${GCS_LOCATION}" \
      --default-storage-class=STANDARD --uniform-bucket-level-access \
      --public-access-prevention --lifecycle-file="${lifecycle}"
  fi
  echo "configured Standard -> Coldline (${COLDLINE_AFTER_DAYS}d) -> Archive (${ARCHIVE_AFTER_DAYS}d)"
  echo "Billing must be enabled on ${project}; lifecycle transitions and reads can incur charges."
}

case "${ZONE}" in
  us-west1-*|us-central1-*|us-east1-*) ;;
  *) echo "REFUSING: zone ${ZONE} is outside the Always Free regions (us-west1|us-central1|us-east1)."
     echo "It would be billed. Set CAPTURE_ZONE to a free-tier zone."; exit 1 ;;
esac

case "${MACHINE}" in
  e2-micro) ;;
  e2-small|e2-medium)
    echo "WARNING: ${MACHINE} is not Always Free. Use it only if collector_runtime proves e2-micro cannot keep up." ;;
  *) echo "REFUSING: CAPTURE_MACHINE_TYPE must be e2-micro, e2-small or e2-medium."; exit 1 ;;
esac

case "${DISK_GB}" in
  ''|*[!0-9]*) echo "REFUSING: CAPTURE_DISK_GB must be an integer from 1 to 30."; exit 1 ;;
esac
if (( DISK_GB < 1 || DISK_GB > 30 )); then
  echo "REFUSING: ${DISK_GB}GB is outside the configured 1-30GB guardrail."
  exit 1
fi

create() {
  project="$(selected_project)"
  require_billing "${project}"
  gc services enable compute.googleapis.com iam.googleapis.com storage.googleapis.com \
    --project="${project}" --quiet
  service_args=(--scopes=default)
  if gc compute instances describe "${NAME}" --zone="${ZONE}" >/dev/null 2>&1; then
    echo "REFUSING: instance ${NAME} already exists in ${ZONE}."
    exit 1
  fi
  existing="$(gc compute instances list \
    --filter='status!=TERMINATED AND machineType:e2-micro' \
    --format='value(name,zone.basename())')"
  if [[ -n "${existing}" ]]; then
    echo "REFUSING: an active e2-micro already exists in this project:"
    echo "${existing}"
    echo "Review free-tier usage before creating another instance."
    exit 1
  fi
  if [[ -n "${GCS_BUCKET}" ]]; then
    if ! gc storage buckets describe "gs://${GCS_BUCKET}" >/dev/null 2>&1; then
      echo "REFUSING: gs://${GCS_BUCKET} does not exist or is inaccessible."
      echo "Run: CAPTURE_GCS_BUCKET=${GCS_BUCKET} bash $0 bucket-create"
      exit 1
    fi
    writer_name="${CAPTURE_SERVICE_ACCOUNT:-btc-capture-writer}"
    writer_sa="${writer_name}@${project}.iam.gserviceaccount.com"
    if ! gc iam service-accounts describe "${writer_sa}" >/dev/null 2>&1; then
      gc iam service-accounts create "${writer_name}" \
        --project="${project}" --display-name="BTC capture immutable GCS writer"
    fi
    gc storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
      --member="serviceAccount:${writer_sa}" --role=roles/storage.objectCreator >/dev/null
    gc storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
      --member="serviceAccount:${writer_sa}" --role=roles/storage.objectViewer >/dev/null
    service_args=(--service-account="${writer_sa}" --scopes=storage-rw)
    echo "granted ${writer_sa} immutable create/read access to gs://${GCS_BUCKET}"
  fi
  echo "creating ${NAME} (${MACHINE}, ${DISK_GB}GB pd-standard) in ${ZONE}"
  gc compute instances create "${NAME}" \
    --zone="${ZONE}" --machine-type="${MACHINE}" \
    --image-family="${IMAGE_FAMILY}" --image-project="${IMAGE_PROJECT}" \
    --boot-disk-size="${DISK_GB}GB" --boot-disk-type=pd-standard \
    "${service_args[@]}" \
    --metadata=enable-oslogin=TRUE

  echo "waiting for ssh"
  until gc compute ssh "${NAME}" --zone="${ZONE}" --command="true" 2>/dev/null; do sleep 5; done

  echo "installing"
  gc compute ssh "${NAME}" --zone="${ZONE}" --command="
    set -e
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-pip python3-venv git
    mkdir -p ~/btc
  "

  echo "uploading capture_app (this app only - the trading app is NOT copied)"
  gc compute scp --recurse --zone="${ZONE}" \
    "$(dirname "$0")" "${NAME}:~/btc/capture_app"

  # Pyth requires API authentication from 2026-08-18. Keep its key out of source,
  # instance metadata and the systemd command line.
  local_env="$(mktemp)"
  trap 'rm -f "${local_env:-}" "${lifecycle:-}"' EXIT
  if [[ -n "${PYTH_KEY}" ]]; then
    case "${PYTH_KEY}" in
      *$'\n'*|*$'\r'*) echo "REFUSING: PYTH_API_KEY may not contain newlines."; exit 1 ;;
    esac
    printf 'PYTH_API_KEY=%s\n' "${PYTH_KEY}" >>"${local_env}"
  fi
  if [[ -n "${PYTH_ENDPOINT_VALUE}" ]]; then
    printf 'PYTH_ENDPOINT=%s\n' "${PYTH_ENDPOINT_VALUE}" >>"${local_env}"
  fi
  gc compute scp --zone="${ZONE}" "${local_env}" "${NAME}:~/btc/capture.env"
  gc compute ssh "${NAME}" --zone="${ZONE}" --command="
    sudo install -o root -g root -m 600 ~/btc/capture.env /etc/btc-capture.env
    rm -f ~/btc/capture.env
  "

  gc compute ssh "${NAME}" --zone="${ZONE}" --command="
    set -e
    cd ~/btc
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r capture_app/requirements.txt
    .venv/bin/python capture_app/run.py --selftest

    sudo tee /etc/systemd/system/btc-capture.service >/dev/null <<UNIT
[Unit]
Description=BTC capture
After=network-online.target
Wants=network-online.target

[Service]
User=\$(whoami)
WorkingDirectory=/home/\$(whoami)/btc
EnvironmentFile=-/etc/btc-capture.env
Environment=CAPTURE_GCS_BUCKET=${GCS_BUCKET}
Environment=CAPTURE_GCS_PREFIX=${GCS_PREFIX}
ExecStart=/home/\$(whoami)/btc/.venv/bin/python capture_app/run.py --record
ExecStartPre=/home/\$(whoami)/btc/.venv/bin/python capture_app/run.py --selftest
Restart=always
RestartSec=10
StandardOutput=append:/home/\$(whoami)/btc/capture.log
StandardError=append:/home/\$(whoami)/btc/capture.log

[Install]
WantedBy=multi-user.target
UNIT

    sudo systemctl daemon-reload
    sudo systemctl enable --now btc-capture

    # Alerting. A stopped stream is a silent hole; this is the thing that makes it loud.
    ( crontab -l 2>/dev/null | grep -v btc-capture-status ; \
      echo '*/15 * * * * cd \$HOME/btc && .venv/bin/python capture_app/run.py --status >> status.log 2>&1 # btc-capture-status' \
    ) | crontab -
    ( crontab -l 2>/dev/null | grep -v btc-capture-quality ; \
      echo '17 * * * * cd \$HOME/btc && .venv/bin/python capture_app/run.py --quality >> quality.log 2>&1 # btc-capture-quality' \
    ) | crontab -
  "
  echo
  echo "started. check in 10 minutes:   bash $0 status"
}

status() {
  gc compute ssh "${NAME}" --zone="${ZONE}" --command="
    cd ~/btc && .venv/bin/python capture_app/run.py --status; echo '--- exit:' \$?
    echo; df -h / | tail -1
  "
}

logs() { gc compute ssh "${NAME}" --zone="${ZONE}" --command="tail -40 ~/btc/capture.log"; }

destroy() { gc compute instances delete "${NAME}" --zone="${ZONE}" --quiet; }

case "${1:-}" in
  bucket-create) bucket_create ;;
  create) create ;;
  status) status ;;
  logs) logs ;;
  destroy) destroy ;;
  *) sed -n '2,25p' "$0"; exit 2 ;;
esac
