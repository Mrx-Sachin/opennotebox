# Cloudflare Tunnel: deepread.mrxsach.in

Exposes the app on this laptop at `http://127.0.0.1:3003` as
**https://deepread.mrxsach.in** via Cloudflare Tunnel (`cloudflared`).
No router port forwarding. The laptop must be on and online, and something
must be listening on port 3003.

## Quick reference

```bash
# start
systemctl --user start cloudflared-deepread

# stop
systemctl --user stop cloudflared-deepread

# restart
systemctl --user restart cloudflared-deepread

# status
systemctl --user status cloudflared-deepread

# live logs
journalctl --user -u cloudflared-deepread -f
```

Public URL: https://deepread.mrxsach.in

## What was set up

| Item | Value |
|------|--------|
| Public hostname | `deepread.mrxsach.in` |
| Local service | `http://127.0.0.1:3003` |
| Tunnel name | `deepread` |
| Tunnel ID | `777e211c-311d-4bf2-b2b6-ec497af85e38` |
| Binary | `~/.local/bin/cloudflared` |
| Tunnel config | `~/.cloudflared/config.yml` |
| Credentials | `~/.cloudflared/777e211c-311d-4bf2-b2b6-ec497af85e38.json` |
| Origin cert (login) | `~/.cloudflared/cert.pem` |
| systemd unit | `~/.config/systemd/user/cloudflared-deepread.service` |

DNS is a Cloudflare CNAME for `deepread` pointing at the tunnel
(`777e211c-311d-4bf2-b2b6-ec497af85e38.cfargotunnel.com`), proxied.

The systemd user service is enabled, so it starts when you log in.
To keep it running after logout (recommended if you want phone access while
the desktop session is closed):

```bash
sudo loginctl enable-linger $USER
```

## Start / stop details

### Start

```bash
systemctl --user start cloudflared-deepread
```

Check it connected:

```bash
systemctl --user status cloudflared-deepread
curl -sI https://deepread.mrxsach.in/ | head -5
```

You want `active (running)` and HTTP 200 from the domain. The app on port
3003 must already be up, or Cloudflare will show an origin error.

### Stop

```bash
systemctl --user stop cloudflared-deepread
```

The public hostname will stop serving until you start the service again.
Your local app on 3003 is unaffected.

### Disable autostart (optional)

```bash
systemctl --user disable cloudflared-deepread
```

Re-enable later with:

```bash
systemctl --user enable --now cloudflared-deepread
```

### Run once without systemd

If you only need a temporary tunnel:

```bash
export PATH="$HOME/.local/bin:$PATH"
cloudflared tunnel --config ~/.cloudflared/config.yml run deepread
```

Ctrl+C stops it. Do not run this while the systemd unit is already active,
or you get two connectors fighting over the same tunnel.

## Files worth knowing

`~/.cloudflared/config.yml` maps the hostname to localhost:

```yaml
tunnel: 777e211c-311d-4bf2-b2b6-ec497af85e38
credentials-file: /home/mrx/.cloudflared/777e211c-311d-4bf2-b2b6-ec497af85e38.json

ingress:
  - hostname: deepread.mrxsach.in
    service: http://127.0.0.1:3003
  - service: http_status:404
```

After editing the config:

```bash
systemctl --user restart cloudflared-deepread
```

Keep `cert.pem` and the tunnel `.json` credentials private. Anyone with them
can register a connector for this tunnel.

## Verify from outside

1. Laptop online, app on `:3003`, tunnel service active.
2. From phone on cellular (not home Wi-Fi), open https://deepread.mrxsach.in
3. Or from any machine:

```bash
curl -sI https://deepread.mrxsach.in/
curl -s https://deepread.mrxsach.in/health
```

## Common problems

**Domain times out or 502.** Tunnel not running, or nothing on 3003.

```bash
systemctl --user status cloudflared-deepread
ss -tlnp | grep 3003
journalctl --user -u cloudflared-deepread -n 50 --no-pager
```

**Service fails after reboot.** User systemd may not be up until login.
Enable linger (see above), or start the unit after you log in.

**`cloudflared` not found in a new shell.** PATH should include
`~/.local/bin` (added to `~/.bashrc`). systemd uses the full path, so the
service does not depend on your interactive PATH.

**Need to recreate DNS.**

```bash
export PATH="$HOME/.local/bin:$PATH"
cloudflared tunnel route dns deepread deepread.mrxsach.in
```

**Need to log in again** (missing `cert.pem`):

```bash
cloudflared tunnel login
```

Authorize the `mrxsach.in` zone in the browser.

## Security

The hostname is on the public internet whenever the tunnel is up. There is
no Cloudflare Access gate in front of it yet. If you only want yourself on
it, add an Access application for `deepread.mrxsach.in` in the Cloudflare
Zero Trust dashboard (email OTP or Google login).

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
cloudflared tunnel create deepread   # skip if tunnel already exists in the account
# write config.yml as above (use the new tunnel id and credentials path)
cloudflared tunnel route dns deepread deepread.mrxsach.in

# then restore the systemd unit from this doc and:
systemctl --user daemon-reload
systemctl --user enable --now cloudflared-deepread
```

If the old tunnel still exists in the Cloudflare dashboard, prefer
`cloudflared tunnel list` and reuse that ID instead of creating a duplicate.
