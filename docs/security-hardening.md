# Public release security review

The intended public path is Cloudflare Tunnel to host loopback port 3003,
Traefik, then the API and frontend. The application still requires its own
authentication. This review is not a penetration test or a guarantee against
unknown vulnerabilities.

## Application changes

- URL ingestion rejects non-global destinations by default, including CGNAT
  and IPv4-mapped private addresses. Redirects are revalidated and DNS addresses
  pinned for each hop. Link-local metadata, multicast, reserved, and unspecified
  addresses remain blocked even when an operator explicitly opts into private
  file servers.
- Multipart uploads reject mismatched PDF/ZIP-format signatures before queuing
  parsing. Signature checking is not a substitute for sandboxing document
  parsers or limiting upload resources.
- A pre-body ASGI upload guard rejects missing, invalid, expired, and refresh
  tokens before multipart buffering. It caps both declared and streamed request
  bytes at the configured upload limit plus 1 MiB of multipart overhead.
  An unexpired token for a revoked account can still buffer a bounded body
  before the database authentication dependency rejects it.
- Administrator demotion and deletion serialize through a PostgreSQL
  transaction advisory lock and cannot remove the last administrator. Ordinary
  updates of the remaining administrator stay permitted.
- API and proxy responses set content-type, framing, referrer, and permissions
  headers. The frontend adds CSP compatible with local PDF workers, WebAssembly,
  and blob audio. Production excludes JavaScript `unsafe-eval`; Next.js still
  requires inline scripts in the current static policy.
- Authentication rate limiting uses the trusted ASGI client address, not an
  arbitrary `CF-Connecting-IP` request header.

Twenty-one focused security checks and the existing authentication/download
regressions validate these changes. Final upload-guard and combined release
checks are recorded in `decision.md`.

## Dependency evidence

- The fresh npm runtime audit initially found Next.js cache-poisoning advisories
  and a source-map-js denial-of-service advisory. Next.js 15.5.27 and
  source-map-js 1.2.2 clear the runtime audit. Development-only advisories remain
  outside the deployed standalone runtime.
- OSV checked 107 Python image distributions. Patched compatible releases are
  constrained in `backend/pyproject.toml` and frozen in `backend/uv.lock`;
  setuptools is explicitly updated in the image. The rebuilt dependency image
  passes 69 focused compatibility tests.
- The remaining unpatched python-jose public-key-confusion and ecdsa timing
  advisories affect operations this application does not use. Authentication
  fixes the algorithm to HS256 with a symmetric secret. Negative algorithm and
  public-key-forgery checks pass. Reassess this finding before adding asymmetric
  JWT algorithms or public-key signing.
- GitHub's upstream Traefik release/advisory metadata verifies version 3.7.14.
  The release pins its image digest and validates routing before deployment.
  Relevant older-proxy findings include request-target interpretation and
  HTTP/2/TLS checks. Request headers with aliasing names are stripped explicitly.
  Sources include [the release](https://github.com/traefik/traefik/releases/tag/v3.7.14)
  and [the request-target advisory](https://github.com/advisories/GHSA-f52w-8j3h-j724).

No comprehensive container operating-system package scan was performed.
Registry and advisory scans are point-in-time checks, not proof of exploitability
or exhaustive coverage.

## Deployment requirements

- Keep registration closed, private-network CORS disabled for this public
  installation, and allowed origins explicit. Preserve the existing JWT secret
  and database credentials in a private environment file.
- Bind direct HTTPS to loopback as well as port 3003. Preserve API port 8001,
  frontend port 3004, database, Redis, and TTS debug ports as loopback-only.
  Outside clients use the existing public hostname, not direct LAN port 443.
- Inject only the current Docker host bridge gateway `/32` into the private
  Traefik `weblocal` trusted-forwarder list. Verify actual client-IP forwarding
  after deployment. Do not put a changing installation subnet in generic config.
- Use immutable application images without source mounts. Preserve existing
  storage and infrastructure volumes and retain a restore-tested backup.
- A Cloudflare Access allowlist is optional additional protection. Configuring
  it requires the account owner's identity/access policy and is not performed
  by this release. Keep edge abuse limits, host patching, backups, and queue/disk
  monitoring operational.

The requirements above are release gates. Deployment completion and live probe
results belong in the append-only decision log, not assumptions in this report.
