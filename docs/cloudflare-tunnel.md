# Cloudflare Tunnel: opennotebox.mrxsach.in

Exposes the app on this laptop at `http://127.0.0.1:3013` as
**https://opennotebox.mrxsach.in** via Cloudflare Tunnel (`cloudflared`).
No router port forwarding. The laptop must be on and online, and something
must be listening on port 3013.

The legacy domain **https://deepread.mrxsach.in** also points to the same
tunnel and continues to work.

## Quick reference

```bash
# start
systemctl --user start cloudflared-opennotebox

# stop
systemctl --user stop cloudflared-opennotebox

# restart
systemctl --user restart cloudflared-opennotebox

# status
systemctl --user status cloudflared-opennotebox

# live logs
journalctl --user -u cloudflared-opennotebox -f
```

Public URLs:
- https://opennotebox.mrxsach.in (primary)
- https://deepread.mrxsach.in (legacy, kept active)

## What was set up

| Item | Value |
|------|--------|
| Public hostname (primary) | `opennotebox.mrxsach.in` |
| Public hostname (legacy) | `deepread.mrxsach.in` |
| Local service | `http://127.0.0.1:3013` |
| Tunnel name | `opennotebox` |
| Tunnel ID | `777e211c-311d-4bf2-b2b6-ec497af85e38` |
| Binary | `~/.local/bin/cloudflared` |
| Tunnel config | `~/.cloudflared/config.yml` |
| Credentials | `~/.cloudflared/777e211c-311d-4bf2-b2b6-ec497af85e38.json` |
| Origin cert (login) | `~/.cloudflared/cert.pem` |
| systemd unit | `~/.config/systemd/user/cloudflared-opennotebox.service` |

DNS is a Cloudflare CNAME for both `opennotebox` and `deepread` pointing at
the tunnel (`777e211c-311d-4bf2-b2b6-ec497af85e38.cfargotunnel.com`), proxied.

The systemd user service is enabled, so it starts when you log in.
To keep it running after logout (recommended if you want phone access while
the desktop session is closed):

```bash
sudo loginctl enable-linger $USER
```

## Start / stop details

### Start

```bash
systemctl --user start cloudflared-opennotebox
```

Check it connected:

```bash
systemctl --user status cloudflared-opennotebox
curl -sI https://opennotebox.mrxsach.in/ | head -5
curl -sI https://deepread.mrxsach.in/ | head -5
```

You want `active (running)` and HTTP 200 from both domains. The app on port
3003 must already be up, or Cloudflare will show an origin error.

### Stop

```bash
systemctl --user stop cloudflared-opennotebox
```

Both hostnames will stop serving until you start the service again.
Your local app on 3013 is unaffected.

### Disable autostart (optional)

```bash
systemctl --user disable cloudflared-opennotebox
```

Re-enable later with:

```bash
systemctl --user enable --now cloudflared-opennotebox
```

### Run once without systemd

If you only need a temporary tunnel:

```bash
export PATH="$HOME/.local/bin:$PATH"
cloudflared tunnel --config ~/.cloudflared/config.yml run opennotebox
```

Ctrl+C stops it. Do not run this while the systemd unit is already active,
or you get two connectors fighting over the same tunnel.

## Files worth knowing

`~/.cloudflared/config.yml` maps both hostnames to localhost:

```yaml
tunnel: 777e211c-311d-4bf2-b2b6-ec497af85e38
credentials-file: /home/mrx/.cloudflared/777e211c-311d-4bf2-b2b6-ec497af85e38.json

ingress:
  - hostname: opennotebox.mrxsach.in
    service: http://127.0.0.1:3013
  - hostname: deepread.mrxsach.in
    service: http://127.0.0.1:3013
  - service: http_status:404
```

After editing the config:

```bash
systemctl --user restart cloudflared-opennotebox
```

Keep `cert.pem` and the tunnel `.json` credentials private. Anyone with them
can register a connector for this tunnel.

## Verify from outside

1. Laptop online, app on `:3013`, tunnel service active.
2. From phone on cellular (not home Wi-Fi), open https://opennotebox.mrxsach.in
3. Or from any machine:

```bash
curl -sI https://opennotebox.mrxsach.in/
curl -s https://opennotebox.mrxsach.in/health
curl -sI https://deepread.mrxsach.in/
```

## Common problems

**Domain times out or 502.** Tunnel not running, or nothing on 3013.

```bash
systemctl --user status cloudflared-opennotebox
ss -tlnp | grep 3003
journalctl --user -u cloudflared-opennotebox -n 50 --no-pager
```

**Service fails after reboot.** User systemd may not be up until login.
Enable linger (see above), or start the unit after you log in.

**`cloudflared` not found in a new shell.** PATH should include
`~/.local/bin` (added to `~/.bashrc`). systemd uses the full path, so the
service does not depend on your interactive PATH.

**Need to recreate DNS.**

```bash
export PATH="$HOME/.local/bin:$PATH"
cloudflared tunnel route dns opennotebox opennotebox.mrxsach.in
cloudflared tunnel route dns opennotebox deepread.mrxsach.in
```

**Need to log in again** (missing `cert.pem`):

```bash
cloudflared tunnel login
```

Authorize the `mrxsach.in` zone in the browser.

## Security

Both hostnames are on the public internet whenever the tunnel is up. There is
no Cloudflare Access gate in front of them yet. If you only want yourself on
it, add an Access application for `opennotebox.mrxsach.in` and
`deepread.mrxsach.in` in the Cloudflare Zero Trust dashboard (email OTP or
Google login).

## One-time recreate (disaster recovery)

Only if the tunnel or machine setup was wiped:

```bash
export PATH="$HOME/.local/bin:$PATH"

# install binary if missing
mkdir -p ~/.local/bin
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o ~/.local/bin/cloudflared
chmod +x ~/.local/bin/cloudflared

cloudflared tunnel login
cloudflared tunnel create opennotebox   # skip if tunnel already exists in the account
# write config.yml as above (use the new tunnel id and credentials path)
cloudflared tunnel route dns opennotebox opennotebox.mrxsach.in
cloudflared tunnel route dns opennotebox deepread.mrxsach.in

# then restore the systemd unit from this doc and:
systemctl --user daemon-reload
systemctl --user enable --now cloudflared-opennotebox
```

If the old tunnel still exists in the Cloudflare dashboard, prefer
`cloudflared tunnel list` and reuse that ID instead of creating a duplicate.
