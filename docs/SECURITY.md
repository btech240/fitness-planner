# Security

Fitness Planner has **no login** on purpose: it's a household app on the home network, and anyone who can reach it can use every feature (pick any profile, log sets, change equipment, delete a profile). What it must *not* allow is anyone on the network — or a malicious website someone in the house visits — doing anything to the **container or host** beyond using the app.

## What protects the container

| Layer | Protection |
|-------|------------|
| **Network exposure** | Only port **8000** (the app) is meant to be reachable. Never port-forward it from your router. Use it on the home network, or from outside through a private VPN such as Tailscale (see below). |
| **Unprivileged LXC** | Root inside the container maps to an unprivileged user on the Proxmox host. |
| **Dedicated service user** | The app runs as `fitness` (no shell, no login), never root. |
| **Read-only code** | `/opt/fitness-planner` is owned by root and not writable by the service, so even a compromised app process can't change its own code. |
| **systemd sandbox** | `ProtectSystem=strict` (whole filesystem read-only except `/var/lib/fitness-planner`), `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, no capabilities, no device access, kernel tunables/modules/logs protected, namespaces/realtime/SUID blocked, only IPv4/IPv6/Unix sockets, and a system-call filter (`@system-service`, no `@privileged`). If a container can't support the extra sandbox, `install.sh` falls back to the basic one and says so. |
| **Private data folder** | `/var/lib/fitness-planner` is `750`, owned by `fitness`; new files are created without world access (`UMask=0027`). |

## What the app itself guards against

| Risk | Guard |
|------|-------|
| **Another website making your browser change things** (cross-site request forgery) | Every change must come from the app's own pages: requests whose `Origin`/`Referer` is another site are refused (403). The profile cookie is `SameSite=Lax` and `HttpOnly`. |
| **DNS rebinding** (a website pretending to *be* the app's address) | The app only answers to the host names/IPs it's reached by (`FP_ALLOWED_HOSTS`, set by `install.sh` to the container's IPs, its hostname and localhost). Others get 400. |
| **Reading other files** | Photos are served only for names matching `<exercise>_<0\|1>.jpg` from the photos folder; static files from the app's own folder. |
| **Injection** | All database queries are parameterized; all page text is HTML-escaped (including profile names). |
| **Information leaks** | No API schema or docs endpoints; the server doesn't announce its software name. |
| **Supply chain** | Python packages are pinned to exact versions; exercise data and photos come from a public-domain repository over HTTPS and are type- and size-checked. The container's GitHub key is **read-only**. |

## What it deliberately does not do

- **No passwords or per-person privacy.** Anyone on the home network can open any profile. Don't store anything sensitive in it.
- **No HTTPS.** Traffic on the home Wi-Fi is unencrypted (workout data only). Don't expose it beyond your LAN except through a VPN.

## Away from home (Tailscale or a VPN)

To use it outside the house, reach it through a private network only your own devices can join, such as Tailscale or WireGuard. Everyone on that private network can use the app just like someone on your home Wi-Fi, so only add people you'd let into your house network.

- **Tailscale inside the container:** install Tailscale in the container *before* running `install.sh` (or re-run it after), so its Tailscale IP is picked up automatically. An unprivileged LXC needs `/dev/net/tun` passed through; see Tailscale's LXC guide.
- **Tailscale subnet router or a VPN to your home network:** nothing to change; you open the same `http://<lxc-ip>:8000` address.
- **Using a name** (e.g. a MagicDNS name like `fitness.tailnet-name.ts.net`): add it to the allowed hosts, or the app answers "Unknown host name": `EXTRA_HOSTS="fitness,fitness.tailnet-name.ts.net" bash /opt/fitness-planner/deploy/install.sh`.

Don't use Tailscale **Funnel** or any other public tunnel: that puts the app on the internet with no login.

## Lock down container access (recommended, do this once)

The app can't protect the container's *own* logins. Port **22 (SSH)** is open on the container and offers password login. You can always get in from the Proxmox host with `pct enter <id>`, so SSH into the container isn't needed. Inside the container, check:

```sh
sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication)'
```

Pick one:

- **Turn SSH off in the container** (simplest; use `pct enter` from the Proxmox host instead):
  ```sh
  systemctl disable --now ssh
  ```
- **Or keep SSH but keys only:**
  ```sh
  printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' > /etc/ssh/sshd_config.d/10-hardening.conf
  systemctl reload ssh
  ```
  (Add your public key to `/root/.ssh/authorized_keys` first if you want to SSH in.)

Also keep the container updated: `apt update && apt upgrade` now and then, and include it in your Proxmox backup job.

## Reaching the app by another name

If you open the app by a name other than its IP (e.g. a router DNS name like `fitness.lan`), add it, then re-run the installer:

```sh
EXTRA_HOSTS="fitness.lan" bash /opt/fitness-planner/deploy/install.sh
```

Otherwise that name gets *"Unknown host name for this app."*
