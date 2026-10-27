#!/usr/bin/env bash
#
# Generate a local certificate authority and a server certificate for Traefik.
#
# Why a local CA rather than a plain self-signed certificate: a self-signed
# leaf cannot be trusted by a device without trusting that exact certificate,
# so every regeneration re-breaks every device. With a CA, a device trusts
# ca.crt once and keeps working across every later reissue of the leaf.
#
# Why certificates at all on a home network: browsers gate a growing list of
# APIs (crypto.randomUUID, navigator.clipboard, service workers, media
# capture) behind "secure contexts", which means HTTPS or localhost. Served
# over plain HTTP at a LAN address those APIs are simply absent, which shows
# up as features that work on the host machine and silently break on a phone.
#
# Re-run at any time; it is idempotent for the CA (reused if present) and
# always reissues the leaf, which is how you add a new address to the SAN
# list. Devices keep trusting it because the CA has not changed.
#
# Usage:
#   ./scripts/gen-certs.sh              # auto-detect this host's addresses
#   EXTRA_SANS="DNS:box.home,IP:10.0.0.5" ./scripts/gen-certs.sh
set -euo pipefail

cd "$(dirname "$0")/.."
CERT_DIR="certs"
CA_DAYS=3650
# Public CAs cap leaf lifetime for revocation reasons that do not apply to a
# CA whose whole trust store is your own machines; a long leaf just avoids a
# yearly outage nobody would have diagnosed quickly.
LEAF_DAYS=3650

mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"

# Every address this host can be reached on, so one certificate covers the
# loopback, LAN and Tailscale paths without needing to know in advance which
# one a given device will use.
sans="DNS:localhost,DNS:opennotebox.local"
host_short="$(hostname -s 2>/dev/null || hostname)"
[ -n "$host_short" ] && sans="$sans,DNS:$host_short,DNS:$host_short.local"
sans="$sans,IP:127.0.0.1,IP:::1"

# Skip Docker's own bridge addresses: they are this host as seen from inside a
# container, never an address a person browses to, and listing seven of them
# just makes the certificate harder to read.
#
# Globally-routable IPv6 (2000::/3) is skipped too, for a different reason: a
# public address is not a local one, this certificate is not meant to vouch
# for the host on the open internet, and an ISP-assigned prefix rotates - so
# including it would churn the certificate for an address nobody browses to.
# Tailscale's fd7a:... is a unique-local address and is kept.
while read -r ifname ip; do
  case "$ifname" in docker*|br-*|veth*) continue ;; esac
  case "$ip" in [23][0-9a-fA-F]??:*) continue ;; esac
  [ -n "$ip" ] && sans="$sans,IP:$ip"
done < <(ip -o addr show scope global 2>/dev/null | awk '{split($4,a,"/"); print $2, a[1]}')

# Tailscale's MagicDNS name, when this host is on a tailnet. Without it the
# only way in over Tailscale is the bare 100.x address, which works but shows
# a name mismatch the moment anyone uses the friendlier hostname.
if command -v tailscale >/dev/null 2>&1; then
  ts_name="$(tailscale status --json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Self",{}).get("DNSName","").rstrip("."))' 2>/dev/null || true)"
  [ -n "$ts_name" ] && sans="$sans,DNS:$ts_name"
fi

[ -n "${EXTRA_SANS:-}" ] && sans="$sans,${EXTRA_SANS}"

echo "Subject alternative names:"
echo "  ${sans//,/$'\n  '}"

if [ -f "$CERT_DIR/ca.crt" ] && [ -f "$CERT_DIR/ca.key" ]; then
  echo "Reusing existing CA ($CERT_DIR/ca.crt) - devices that already trust it stay trusted."
else
  echo "Creating a new CA..."
  openssl req -x509 -newkey rsa:4096 -sha256 -days "$CA_DAYS" -nodes \
    -keyout "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt" \
    -subj "/CN=OpenNoteBox Local CA/O=OpenNoteBox" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
fi

echo "Issuing server certificate..."
openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" \
  -subj "/CN=opennotebox.local/O=OpenNoteBox" 2>/dev/null

openssl x509 -req -in "$CERT_DIR/server.csr" -sha256 -days "$LEAF_DAYS" \
  -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
  -out "$CERT_DIR/server.crt" \
  -extfile <(printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' "$sans") 2>/dev/null

rm -f "$CERT_DIR/server.csr"
# Traefik runs as root in its container and reads these read-only; 600 keeps
# the private keys off-limits to other users on the host.
chmod 600 "$CERT_DIR"/*.key
chmod 644 "$CERT_DIR"/*.crt

echo
echo "Done. Files in $CERT_DIR/:"
ls -1 "$CERT_DIR"
echo
echo "Install $CERT_DIR/ca.crt on each device you browse from to get a green"
echo "padlock. Without it you get a one-time click-through warning; HTTPS is"
echo "real either way, so the secure-context APIs work regardless."
