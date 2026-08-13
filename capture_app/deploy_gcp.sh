#!/usr/bin/env bash
# Provision a GCP Always-Free e2-micro and run the capture app on it.
#
# RUN THIS YOURSELF. It creates billable-capable resources under your account.
#
#   bash capture_app/deploy_gcp.sh create     # make the VM and start capture
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
MACHINE="e2-micro"
DISK_GB="${CAPTURE_DISK_GB:-30}"
IMAGE_FAMILY="debian-12"
IMAGE_PROJECT="debian-cloud"

case "${ZONE}" in
  us-west1-*|us-central1-*|us-east1-*) ;;
  *) echo "REFUSING: zone ${ZONE} is outside the Always Free regions (us-west1|us-central1|us-east1)."
     echo "It would be billed. Set CAPTURE_ZONE to a free-tier zone."; exit 1 ;;
esac

create() {
  echo "creating ${NAME} (${MACHINE}, ${DISK_GB}GB pd-standard) in ${ZONE}"
  gcloud compute instances create "${NAME}" \
    --zone="${ZONE}" --machine-type="${MACHINE}" \
    --image-family="${IMAGE_FAMILY}" --image-project="${IMAGE_PROJECT}" \
    --boot-disk-size="${DISK_GB}GB" --boot-disk-type=pd-standard \
    --scopes=storage-rw \
    --metadata=enable-oslogin=TRUE

  echo "waiting for ssh"
  until gcloud compute ssh "${NAME}" --zone="${ZONE}" --command="true" 2>/dev/null; do sleep 5; done

  echo "installing"
  gcloud compute ssh "${NAME}" --zone="${ZONE}" --command="
    set -e
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-pip python3-venv git
    mkdir -p ~/btc
  "

  echo "uploading capture_app (this app only - the trading app is NOT copied)"
  gcloud compute scp --recurse --zone="${ZONE}" \
    "$(dirname "$0")" "${NAME}:~/btc/capture_app"

  gcloud compute ssh "${NAME}" --zone="${ZONE}" --command="
    set -e
    cd ~/btc
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r capture_app/requirements.txt

    sudo tee /etc/systemd/system/btc-capture.service >/dev/null <<UNIT
[Unit]
Description=BTC capture
After=network-online.target
Wants=network-online.target

[Service]
User=\$(whoami)
WorkingDirectory=/home/\$(whoami)/btc
ExecStart=/home/\$(whoami)/btc/.venv/bin/python capture_app/run.py --record
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
  "
  echo
  echo "started. check in 10 minutes:   bash $0 status"
}

status() {
  gcloud compute ssh "${NAME}" --zone="${ZONE}" --command="
    cd ~/btc && .venv/bin/python capture_app/run.py --status; echo '--- exit:' \$?
    echo; df -h / | tail -1
  "
}

logs() { gcloud compute ssh "${NAME}" --zone="${ZONE}" --command="tail -40 ~/btc/capture.log"; }

destroy() { gcloud compute instances delete "${NAME}" --zone="${ZONE}" --quiet; }

case "${1:-}" in
  create) create ;;
  status) status ;;
  logs) logs ;;
  destroy) destroy ;;
  *) sed -n '2,25p' "$0"; exit 2 ;;
esac
