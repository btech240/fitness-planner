# Deploy Rackmount to a Proxmox LXC (Debian 12)

Home-network only, no Docker. About 10 minutes.

> **Tested setup:** this Proxmox LXC route is the only one the author has run and tested.
> **Not Proxmox?** On any other Debian 12 or Ubuntu machine (a VM, mini-PC, Raspberry Pi), skip step 1 and run steps 2–5 as root. That should work, but it's **untested**. For Docker, see the [README](../README.md#docker-untested), also **untested**.

## 1. Create the container (Proxmox host shell)

```sh
pveam update
pveam available --section system | grep debian-12          # note the exact template name
pveam download local debian-12-standard_12.12-1_amd64.tar.zst   # use the name shown above

pct create 120 local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst \
  --hostname fitness --unprivileged 1 --features nesting=1 \
  --cores 1 --memory 512 --swap 512 --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --onboot 1 --password
pct start 120
pct enter 120
```

Pick a free CT ID instead of `120`, and your storage names if not `local` / `local-lvm`.
The web UI's **Create CT** wizard works too: Debian 12 template, 1 core, 512 MB RAM, 4 GB disk, DHCP, unprivileged, start at boot.

Give the container a fixed address: a DHCP reservation on your router (or `ip=192.168.x.y/24,gw=...` above).

## 2. Inside the container

```sh
apt update && apt install -y git openssh-client
timedatectl set-timezone America/New_York        # your timezone; used for dates in History
```

## 3. Read-only deploy key for the private repo

```sh
ssh-keygen -t ed25519 -f ~/.ssh/fitness_deploy -N "" -C "fitness-lxc"
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/fitness_deploy
  IdentitiesOnly yes
EOF
cat ~/.ssh/fitness_deploy.pub
```

On GitHub: repo **Settings → Deploy keys → Add deploy key**, paste the public key, leave **Allow write access** unticked.

## 4. Clone and install

```sh
git clone git@github.com:<owner>/rackmount.git /opt/fitness-planner   # answer "yes" to the host key prompt
bash /opt/fitness-planner/deploy/install.sh
```

It prints the address to open, e.g. `http://192.168.x.y:8000`, and whether the full service sandbox is active. On a phone: open the address, Share → **Add to Home Screen**, then **Add person** and fill in **Settings → Equipment**. Hand the [user guide](USER_GUIDE.md) to everyone else.

## 5. Lock down container access

The app runs sandboxed as its own user, but the container's own SSH login is up to you. See **[SECURITY.md → Lock down container access](SECURITY.md#lock-down-container-access-recommended-do-this-once)**: either turn SSH off (`systemctl disable --now ssh`, use `pct enter` from the host) or make it key-only.

## Day to day

| Task | Command (inside the container) |
|------|------|
| Update to latest | `bash /opt/fitness-planner/deploy/update.sh` (pulls, then re-runs the installer: packages, photos, service, restart) |
| Status / logs | `systemctl status fitness-planner` · `journalctl -u fitness-planner -f` |
| Restart | `systemctl restart fitness-planner` |
| Reach it by a name, not just the IP | `EXTRA_HOSTS="fitness.lan" bash /opt/fitness-planner/deploy/install.sh` (remembered for later updates) |
| Different port | `PORT=8080 bash /opt/fitness-planner/deploy/install.sh` |

**Data:** `/var/lib/fitness-planner/fitness.db`. Include the container in your Proxmox backup job. The one-time upgrade to household profiles saved the single-user database beside it as `fitness.pre-profiles-<date>.db` before starting everyone fresh.

**Exercise photos:** `install.sh` and `update.sh` download the start/end form photos (about 950 JPEGs, ~60 MB) into `/var/lib/fitness-planner/photos`; later runs only fetch what's missing. If GitHub is unreachable the app still works and How to shows text only.

**Bring test data from elsewhere** (optional): `systemctl stop fitness-planner`, copy the file to `/var/lib/fitness-planner/fitness.db`, `chown fitness:fitness` it, `systemctl start fitness-planner`.
