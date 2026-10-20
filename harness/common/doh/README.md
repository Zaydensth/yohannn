# DNS-over-HTTPS workaround for a flaky resolver (per-process, nothing system-wide)

Symptom: `curl: Could not resolve host: rest.<provider>.io` / `ssh: Could not resolve hostname ssh.<provider>.io`
from the operator machine while other names resolve, and it changes hour to hour. A chain step or a watcher that
needs one of those names then fails or — worse — a watcher that must STOP a pod cannot reach the API.

Fix used here (VERIFIED on macOS curl 8.x): resolve through DNS-over-HTTPS addressed **by IP**, so no bootstrap
DNS is needed at all. Nothing in `/etc/hosts` or the system resolver is touched; the environment is per process.

- `curlrc.example` → copy to a directory and point `CURL_HOME` at it; curl reads `$CURL_HOME/.curlrc` for every
  call in that process, including `curl -K -` calls that pass the API key on stdin.
- `ssh` → a wrapper placed first on `PATH` for the chain/watcher process only. It rewrites a destination
  `*@ssh.<provider>.io` to `-o HostName=<A record from DoH> -o HostKeyAlias=ssh.<provider>.io`, so the existing
  `known_hosts` entry still matches; every other ssh call passes through untouched. Edit the host name and the
  DoH URL at the top before use.

```bash
export PATH=/path/to/doh/bin:$PATH CURL_HOME=/path/to/doh
```

Rules learned the hard way:
- Use `https://1.1.1.1/dns-query` (an IP), not `https://cloudflare-dns.com/dns-query`: the resolver that fails
  for the provider's name can also fail for the DoH host name, and then every call "fails on transport".
- **Restart the watchers with the same environment.** A watcher launched before the fix keeps the broken
  resolver and cannot stop a billed pod.
- Python's `socket.gethostbyname` does not use DoH: scripts that must survive the outage call curl, or receive
  the IP from the wrapper.
