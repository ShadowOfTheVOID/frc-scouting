#!/usr/bin/env bash
# Keep asking Oracle for an Always Free server until one is free.
#
# Oracle's free shapes are often "out of capacity", and the console's answer is
# "try again later". This is "later", once a minute, in Oracle Cloud Shell -
# the >_ icon at the top right of the console, which already has the oci tool
# signed in as you. Nothing to install:
#
#   curl -fsSL https://raw.githubusercontent.com/ShadowOfTheVOID/frc-scouting/main/mirror/deploy/oracle-retry.sh | bash
#
# It needs the network from setup.md first: a VCN with a subnet named
# public-subnet. It alternates between the two free shapes, stops the moment
# one is created, and prints the public IP. It never creates anything that is
# not Always Free, and refuses to make a second server if one called
# scouting-mirror already exists.
#
# The SSH key it makes lives in Cloud Shell at ~/.ssh/oracle, so log in from
# Cloud Shell (it prints the line), or download the key from Cloud Shell's menu.
set -uo pipefail

NAME=scouting-mirror
SUBNET_NAME=public-subnet
WAIT=60

C="${OCI_TENANCY:-}"
if [ -z "$C" ] && [ -r /etc/oci/config ]; then
  C=$(sed -n 's/^tenancy *= *//p' /etc/oci/config | head -n1)
fi
[ -n "$C" ] || { echo "Could not tell which Oracle account this is - run it in Cloud Shell."; exit 1; }

existing=$(oci compute instance list --compartment-id "$C" --display-name "$NAME" \
  --query 'data[?"lifecycle-state"!=`TERMINATED` && "lifecycle-state"!=`TERMINATING`] | length(@)' \
  --raw-output 2>/dev/null || echo 0)
if [ "${existing:-0}" != "0" ]; then
  echo "There is already a server called $NAME - look in Compute > Instances. Stopping."
  exit 0
fi

AD=$(oci iam availability-domain list --compartment-id "$C" --query 'data[0].name' --raw-output)
SUBNET=$(oci network subnet list --compartment-id "$C" --display-name "$SUBNET_NAME" \
  --query 'data[0].id' --raw-output)
if [ -z "$SUBNET" ] || [ "$SUBNET" = "null" ]; then
  echo "No subnet called $SUBNET_NAME. Make the network first (setup.md, Part B)."
  exit 1
fi

image_for() {   # newest Ubuntu 24.04 for a shape, never the Minimal build
  oci compute image list --compartment-id "$C" --operating-system "Canonical Ubuntu" \
    --operating-system-version "24.04" --shape "$1" --sort-by TIMECREATED --sort-order DESC \
    --query 'data[?!contains("display-name", `Minimal`)] | [0].id' --raw-output
}
IMG_ARM=$(image_for VM.Standard.A1.Flex)
IMG_AMD=$(image_for VM.Standard.E2.1.Micro)

mkdir -p ~/.ssh
[ -f ~/.ssh/oracle ] || ssh-keygen -q -t ed25519 -N "" -f ~/.ssh/oracle -C "$NAME" \
  || { echo "Could not make an SSH key."; exit 1; }

launch() {      # shape, image, extra args...
  local shape=$1 img=$2; shift 2
  oci compute instance launch --availability-domain "$AD" --compartment-id "$C" \
    --display-name "$NAME" --shape "$shape" --image-id "$img" "$@" \
    --subnet-id "$SUBNET" --assign-public-ip true \
    --ssh-authorized-keys-file ~/.ssh/oracle.pub \
    --wait-for-state RUNNING --query 'data.id' --raw-output 2>&1
}

try=0
while :; do
  try=$((try + 1))
  for shape in VM.Standard.A1.Flex VM.Standard.E2.1.Micro; do
    if [ "$shape" = VM.Standard.A1.Flex ]; then
      out=$(launch "$shape" "$IMG_ARM" --shape-config '{"ocpus":1,"memoryInGBs":6}')
    else
      out=$(launch "$shape" "$IMG_AMD")
    fi
    if [[ "$out" == ocid1.instance.* ]]; then
      IP=$(oci compute instance list-vnics --instance-id "$out" \
        --query 'data[0]."public-ip"' --raw-output)
      echo
      echo "Got one: $shape, public IP $IP"
      echo "Log in from this Cloud Shell with:"
      echo "  ssh -i ~/.ssh/oracle ubuntu@$IP"
      exit 0
    fi
    case "$out" in
      *[Cc]apacity*)      echo "$(date +%H:%M) try $try: $shape is full" ;;
      *TooManyRequests*)  echo "$(date +%H:%M) try $try: Oracle says slow down"; sleep "$WAIT" ;;
      *)                  echo "Oracle refused for another reason - this will not fix itself:"
                          echo "$out" | tail -n 15
                          exit 1 ;;
    esac
  done
  sleep "$WAIT"
done
