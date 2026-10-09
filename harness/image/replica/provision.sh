#!/usr/bin/env bash
# provision.sh - idempotent bootstrap of a fresh Ubuntu 1xH100 box for a validator-replica session.
# Run ON the box as root:   bash provision.sh           checks, installs what is missing, creates dirs, prints a report
#                           DRY=1 bash provision.sh     checks only - changes nothing (no apt, no docker, no mkdir)
# From your workstation:  ssh root@BOX 'cat > /root/provision.sh' < provision.sh && ssh root@BOX 'bash /root/provision.sh'
# Safe to re-run: every step checks first and only acts on a gap. It NEVER reboots or powers off the box, never prunes
# docker, never deletes a file, and never restarts the docker daemon while a container is running (the box's power and
# lifecycle belong to its owner, not to a script).
#
# On exit 0 the box has:
#   * apt tools: screen tmux git curl ca-certificates unzip rsync python3 flock(util-linux) timeout(coreutils) ps(procps)
#   * a running docker daemon, the NVIDIA driver (nvidia-smi), and nvidia-container-toolkit wired into docker - proven by
#     a `docker run --gpus device=0 ... nvidia-smi -L` smoke that sees the GPU from INSIDE a container
#   * docker network internal_bridge with Internal=true. G.O.D trainer/runtime.py:71-76 (ensure_internal_network) gives
#     the tournament trainer container NO route out; a replica on the default bridge would let a run pass that fails
#     in production (any download during training)
#   * dirs $KIT_ROOT/{ops,data,api,repo,runs,locks,logs} + latent/{hf_cache,logs,anchors} + cache/{models,hf_cache,datasets}
#   * the single GPU lock file $KIT_ROOT/locks/gpu.lock
#   * a report: GPU, driver, disk, RAM, CPUs, HF token status (the token value is never printed)
#
# FLOCK CONVENTION (score_latent.sh step 2, cell_run.sh): every job that uses the GPU opens $KIT_ROOT/locks/gpu.lock on
# fd 9 and holds it for its WHOLE life - `flock -n 9` (refuse if busy) or `flock -w N 9` (queue) - taken BEFORE any
# setup work. Waiting on `docker ps` instead leaks: the next job slips in while the previous one is still preparing.
# CPU/network-only jobs do NOT take it: warm_cache.sh, build_eval_image.sh, stage.sh transfers.
#
# env (defaults in brackets):
#   DRY [0]            1 = report only
#   KIT_ROOT [/root/kit]
#   HF_ENV_FILE [/root/.hf_env]   token file, parsed with a regex (never sourced). Put it there yourself:
#                      ssh root@BOX 'umask 077; cat > /root/.hf_env'   then paste HF_TOKEN=hf_... and press Ctrl-D
#   GATED_REPOS []     space-separated "owner/name:path/in/repo" entries whose gate the token must pass (e.g. the gated
#                      base model of a family you will train or score); each is probed with a 1-byte ranged GET
#   NVIDIA_REPO [0]    1 = allowed to add NVIDIA's official apt repo to install nvidia-container-toolkit when it is
#                      missing. 0 = print the exact commands and FAIL (adding a third-party apt source is your call)
#   MIN_FREE_GB [500]  WARN when less is free under $KIT_ROOT (what you need depends on the families you stage)
#   SMOKE_IMAGE [ubuntu:22.04]   tiny image for the GPU-in-container smoke (pulled once)
set -uo pipefail

DRY="${DRY:-0}"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
GATED_REPOS="${GATED_REPOS:-}"
HF_ENV_FILE="${HF_ENV_FILE:-/root/.hf_env}"
NVIDIA_REPO="${NVIDIA_REPO:-0}"
MIN_FREE_GB="${MIN_FREE_GB:-500}"
SMOKE_IMAGE="${SMOKE_IMAGE:-ubuntu:22.04}"
case "$DRY" in 0|1) ;; *) echo "DRY must be 0 or 1" >&2; exit 2 ;; esac
case "$NVIDIA_REPO" in 0|1) ;; *) echo "NVIDIA_REPO must be 0 or 1" >&2; exit 2 ;; esac
[[ "$MIN_FREE_GB" =~ ^[0-9]+$ ]] || { echo "MIN_FREE_GB must be an integer" >&2; exit 2; }

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS=""   # lines "STATUS<TAB>item<TAB>detail"
rec() { RESULTS="${RESULTS}$1	$2	$3
"; printf '[provision] %-4s %-26s %s\n' "$1" "$2" "$3"; }
say() { printf '[provision] %s\n' "$*"; }
act() {  # run a state-changing command, or print it under DRY. stdin is /dev/null so that apt/dpkg can never eat the
         # rest of this script when it is fed through `ssh BOX 'bash -s' < provision.sh`
  if [ "$DRY" = "1" ]; then printf '[provision] DRY would run: %s\n' "$*"; return 0; fi
  say "run: $*"; "$@" < /dev/null
}

say "start $TS dry=$DRY kit_root=$KIT_ROOT"

# ---------------------------------------------------------------------------------------------------------------------
# 0. platform + privileges
# ---------------------------------------------------------------------------------------------------------------------
if [ "$(uname -s)" = "Linux" ]; then
  OS_NAME="$(grep -E '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d= -f2- | tr -d '"')"
  case "$OS_NAME" in Ubuntu*) rec PASS os "$OS_NAME kernel=$(uname -r)" ;; *) rec WARN os "not Ubuntu: '${OS_NAME:-unknown}' (apt steps may not apply)" ;; esac
else
  rec FAIL os "$(uname -s): this script provisions the Linux GPU box, not this machine"
fi
if [ "$(id -u)" = "0" ]; then rec PASS root "uid 0"
elif [ "$DRY" = "1" ]; then rec WARN root "not root (DRY only reports)"
else rec FAIL root "run as root: the kit lives under $KIT_ROOT and talks to the docker daemon"; fi
CAN_ACT=1
case "$RESULTS" in *"FAIL	os"*|*"FAIL	root"*) CAN_ACT=0 ;; esac

# ---------------------------------------------------------------------------------------------------------------------
# 1. apt tools (command -> package). Only missing ones are installed; apt-get update only when something is missing.
# ---------------------------------------------------------------------------------------------------------------------
MISSING_PKGS=""
for pair in screen:screen tmux:tmux git:git curl:curl unzip:unzip rsync:rsync python3:python3 flock:util-linux \
            timeout:coreutils sha256sum:coreutils ps:procps; do
  cmd="${pair%%:*}"; pkg="${pair#*:}"
  command -v "$cmd" >/dev/null 2>&1 || case " $MISSING_PKGS " in *" $pkg "*) ;; *) MISSING_PKGS="$MISSING_PKGS $pkg" ;; esac
done
[ -s /etc/ssl/certs/ca-certificates.crt ] || MISSING_PKGS="$MISSING_PKGS ca-certificates"
if [ -n "$MISSING_PKGS" ] && [ "$CAN_ACT" = "1" ] && command -v apt-get >/dev/null 2>&1; then
  act env DEBIAN_FRONTEND=noninteractive apt-get update -q
  # shellcheck disable=SC2086
  act env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends $MISSING_PKGS
fi
STILL=""
for cmd in screen tmux git curl unzip rsync python3 flock timeout sha256sum ps; do
  command -v "$cmd" >/dev/null 2>&1 || STILL="$STILL $cmd"
done
if [ -z "$STILL" ]; then rec PASS apt-tools "screen tmux git curl unzip rsync python3 flock timeout sha256sum ps"
elif [ "$DRY" = "1" ]; then rec WARN apt-tools "missing:$STILL (would install:${MISSING_PKGS:- none})"
else rec FAIL apt-tools "still missing:$STILL"; fi
if command -v python3 >/dev/null 2>&1; then
  PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
  if python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then rec PASS python3 "$PYV (latent_tool.py/board.py/verify_stage.py need >=3.8, stdlib only)"
  else rec FAIL python3 "$PYV < 3.8"; fi
fi

# ---------------------------------------------------------------------------------------------------------------------
# 2. docker daemon (Ubuntu's docker.io only when docker is entirely absent)
# ---------------------------------------------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1 && [ "$CAN_ACT" = "1" ] && command -v apt-get >/dev/null 2>&1; then
  act env DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io
  command -v systemctl >/dev/null 2>&1 && act systemctl enable --now docker
fi
DOCKER_OK=0
if command -v docker >/dev/null 2>&1; then
  if ! docker info >/dev/null 2>&1 && [ "$CAN_ACT" = "1" ] && command -v systemctl >/dev/null 2>&1; then
    act systemctl start docker
  fi
  if docker info >/dev/null 2>&1; then
    DOCKER_OK=1
    rec PASS docker "server $(docker version -f '{{.Server.Version}}' 2>/dev/null) root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null)"
  else rec FAIL docker "installed but the daemon does not answer (docker info)"; fi
else
  if [ "$DRY" = "1" ]; then rec WARN docker "absent (would apt-get install docker.io)"; else rec FAIL docker "absent"; fi
fi

# ---------------------------------------------------------------------------------------------------------------------
# 3. NVIDIA driver + GPU shape (image tasks train on 1x H100: G.O.D validator/tournament/gpu_requirements.py:45-46)
# ---------------------------------------------------------------------------------------------------------------------
GPU_OK=0
if command -v nvidia-smi >/dev/null 2>&1 && GPUS="$(nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader 2>/dev/null)" && [ -n "$GPUS" ]; then
  GPU_OK=1
  NGPU="$(printf '%s\n' "$GPUS" | grep -c .)"
  printf '%s\n' "$GPUS" | sed 's/^/[provision]      gpu: /'
  case "$GPUS" in *H100*) rec PASS gpu "$NGPU GPU(s): $(printf '%s' "$GPUS" | head -n1 | cut -d, -f2 | sed 's/^ //')" ;;
                  *) rec WARN gpu "not an H100 - timings and fp8 behaviour will differ from production training" ;; esac
  [ "$NGPU" = "1" ] || rec WARN gpu-count "$NGPU GPUs: the tournament gives 1; every kit script pins GPU_DEVICE=0"
  RUNNING_GPU="$(nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null | grep -c . || true)"
  [ "${RUNNING_GPU:-0}" = "0" ] && rec PASS gpu-idle "no compute process" || rec WARN gpu-idle "$RUNNING_GPU compute process(es) already on the GPU"
else
  rec FAIL gpu "nvidia-smi missing or failing - the NVIDIA driver is the provider's image; not installed by this script"
fi

# ---------------------------------------------------------------------------------------------------------------------
# 4. nvidia-container-toolkit, wired into docker, proven from inside a container
# ---------------------------------------------------------------------------------------------------------------------
has_runtime() { docker info -f '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; }
if [ "$DOCKER_OK" = "1" ] && ! command -v nvidia-ctk >/dev/null 2>&1; then
  if [ "$NVIDIA_REPO" = "1" ] && [ "$CAN_ACT" = "1" ]; then
    # NVIDIA's documented apt install (docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
    act bash -c 'curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg'
    act bash -c "curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list"
    act env DEBIAN_FRONTEND=noninteractive apt-get update -q
    act env DEBIAN_FRONTEND=noninteractive apt-get install -y -q nvidia-container-toolkit
  else
    say "nvidia-container-toolkit is missing. Either re-run with NVIDIA_REPO=1, or run NVIDIA's documented install:"
    say "  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
    say "  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list"
    say "  apt-get update && apt-get install -y nvidia-container-toolkit && nvidia-ctk runtime configure --runtime=docker && systemctl restart docker"
  fi
fi
if [ "$DOCKER_OK" = "1" ] && command -v nvidia-ctk >/dev/null 2>&1 && ! has_runtime; then
  act nvidia-ctk runtime configure --runtime=docker
  if [ -n "$(docker ps -q 2>/dev/null)" ]; then
    rec FAIL docker-restart "nvidia runtime configured but NOT active: containers are running, refusing to restart docker"
  else
    act systemctl restart docker
  fi
fi
if [ "$DOCKER_OK" = "1" ] && command -v nvidia-ctk >/dev/null 2>&1 && has_runtime; then
  rec PASS nvidia-toolkit "$(nvidia-ctk --version 2>/dev/null | head -n1)"
  if [ "$GPU_OK" = "1" ]; then
    if SMOKE="$(docker run --rm --gpus device=0 "$SMOKE_IMAGE" nvidia-smi -L 2>&1 < /dev/null)" && printf '%s' "$SMOKE" | grep -q '^GPU 0:'; then
      rec PASS gpu-in-docker "$(printf '%s' "$SMOKE" | head -n1)"
    else
      rec FAIL gpu-in-docker "docker run --gpus device=0 $SMOKE_IMAGE nvidia-smi -L failed: $(printf '%s' "$SMOKE" | tail -n1)"
    fi
  fi
elif [ "$DRY" = "1" ]; then rec WARN nvidia-toolkit "absent or not wired into docker (checked only)"
else rec FAIL nvidia-toolkit "absent or not wired into docker - see the commands printed above"; fi

# ---------------------------------------------------------------------------------------------------------------------
# 5. internal_bridge (trainer/constants.py INTERNAL_BRIDGE_NAME; runtime.py:71-76 creates it with internal=True)
# ---------------------------------------------------------------------------------------------------------------------
if [ "$DOCKER_OK" = "1" ]; then
  if ! docker network inspect internal_bridge >/dev/null 2>&1; then act docker network create --internal internal_bridge >/dev/null; fi
  INTERNAL="$(docker network inspect -f '{{.Internal}}' internal_bridge 2>/dev/null || true)"
  if [ "$INTERNAL" = "true" ]; then rec PASS internal-bridge "exists, Internal=true (no route out, like production)"
  elif [ -z "$INTERNAL" ] && [ "$DRY" = "1" ]; then rec WARN internal-bridge "absent (would create with --internal)"
  else rec FAIL internal-bridge "exists with Internal=$INTERNAL - a trainer on it would reach the internet; recreate it by hand"; fi
fi

# ---------------------------------------------------------------------------------------------------------------------
# 6. directories + GPU lock file
# ---------------------------------------------------------------------------------------------------------------------
DIRS="ops data api repo runs locks logs latent latent/hf_cache latent/logs latent/anchors cache cache/models cache/hf_cache cache/datasets"
if [ "$CAN_ACT" = "1" ] || [ "$DRY" = "1" ]; then
  for d in $DIRS; do [ -d "$KIT_ROOT/$d" ] || act mkdir -p "$KIT_ROOT/$d"; done
  [ -e "$KIT_ROOT/locks/gpu.lock" ] || act touch "$KIT_ROOT/locks/gpu.lock"
fi
MISSING_DIRS=""
for d in $DIRS; do [ -d "$KIT_ROOT/$d" ] || MISSING_DIRS="$MISSING_DIRS $d"; done
if [ -z "$MISSING_DIRS" ]; then rec PASS dirs "$KIT_ROOT/{$(printf '%s' "$DIRS" | tr ' ' ',')}"
elif [ "$DRY" = "1" ]; then rec WARN dirs "would create:$MISSING_DIRS"
else rec FAIL dirs "missing:$MISSING_DIRS"; fi
if [ -e "$KIT_ROOT/locks/gpu.lock" ] && command -v flock >/dev/null 2>&1; then
  if ( exec 9>>"$KIT_ROOT/locks/gpu.lock"; flock -n 9 ); then rec PASS gpu-lock "$KIT_ROOT/locks/gpu.lock free"
  else rec WARN gpu-lock "$KIT_ROOT/locks/gpu.lock is HELD by a running job"; fi
fi

# ---------------------------------------------------------------------------------------------------------------------
# 7. capacity: disk under $KIT_ROOT and docker root, RAM (trainer container gets 110g), CPUs (24)
#    (G.O.D trainer/constants.py:34-35 MEMORY_PER_GPU_GB=110, CPUS_PER_GPU=24; runtime.py:79-90)
# ---------------------------------------------------------------------------------------------------------------------
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024/1024}'; }
probe="$KIT_ROOT"; [ -d "$probe" ] || probe="$(dirname "$KIT_ROOT")"; [ -d "$probe" ] || probe=/
FREE="$(free_gb "$probe")"
if [ -n "$FREE" ] && [ "$FREE" -ge "$MIN_FREE_GB" ]; then rec PASS disk "${FREE} GB free under $probe"
else rec WARN disk "${FREE:-?} GB free under $probe (< $MIN_FREE_GB GB)"; fi
if [ "$DOCKER_OK" = "1" ]; then
  DROOT="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null)"; DFREE="$(free_gb "${DROOT:-/}")"
  rec INFO docker-disk "${DFREE:-?} GB free under ${DROOT:-?} (the evaluator and trainer images live here)"
fi
if [ -r /proc/meminfo ]; then
  MEM_GB="$(awk '/^MemTotal:/ {printf "%d", $2/1024/1024}' /proc/meminfo)"
  if [ "${MEM_GB:-0}" -ge 120 ]; then rec PASS ram "${MEM_GB} GB (trainer container is capped at 110g like production)"
  else rec WARN ram "${MEM_GB} GB < 120 GB: a 110g trainer container cannot be production-shaped here"; fi
fi
NCPU="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)"
if [ "$NCPU" -ge 24 ] 2>/dev/null; then rec PASS cpus "$NCPU (trainer gets 24 like production)"; else rec WARN cpus "$NCPU < 24"; fi

# ---------------------------------------------------------------------------------------------------------------------
# 8. HF token: presence, 0600, validity, and (GATED_REPOS) gate access. The value is never printed or put on a command
#    line. A gated base model needs it for the evaluator AND for the train-side downloader.
# ---------------------------------------------------------------------------------------------------------------------
if [ -f "$HF_ENV_FILE" ] && grep -qE 'hf_[A-Za-z0-9]{20,}' "$HF_ENV_FILE" 2>/dev/null; then
  PERM="$(stat -c '%a' "$HF_ENV_FILE" 2>/dev/null || stat -f '%Lp' "$HF_ENV_FILE" 2>/dev/null)"
  if [ "$PERM" != "600" ]; then act chmod 600 "$HF_ENV_FILE"; fi
  if command -v python3 >/dev/null 2>&1; then
    HF_REPORT="$(HF_ENV_FILE="$HF_ENV_FILE" GATED_REPOS="$GATED_REPOS" python3 - <<'PY'
import os, re, json, urllib.request, urllib.error
tok = re.search(r"hf_[A-Za-z0-9]{20,}", open(os.environ["HF_ENV_FILE"]).read()).group(0)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
def call(url, extra=None, follow=True):
    req = urllib.request.Request(url, headers=dict({"Authorization": "Bearer " + tok}, **(extra or {})))
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.read(4096)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return type(e).__name__, b""
code, body = call("https://huggingface.co/api/whoami-v2")
if code != 200:
    print("FAIL\thf-token\tinvalid (whoami-v2 -> %s)" % code)
    raise SystemExit
name = json.loads(body or b"{}").get("name", "?")
print("PASS\thf-token\tvalid, account=%s" % name)
for spec in os.environ.get("GATED_REPOS", "").split():
    repo, _, path = spec.partition(":")
    if not repo or not path:
        print("FAIL\tgated-repo\t%r is not owner/name:path/in/repo" % spec)
        continue
    code, _ = call("https://huggingface.co/%s/resolve/main/%s" % (repo, path), {"Range": "bytes=0-0"}, follow=False)
    if code in (200, 206, 302, 303, 307):
        print("PASS\tgated:%s\treadable (HTTP %s)" % (repo, code))
    else:
        print("FAIL\tgated:%s\tNOT readable (HTTP %s): accept the repo's terms on huggingface.co with account %s" % (repo, code, name))
PY
)"
    printf '%s\n' "$HF_REPORT" | while IFS='	' read -r st item detail; do [ -n "$st" ] && printf '[provision] %-4s %-26s %s\n' "$st" "$item" "$detail"; done
    RESULTS="${RESULTS}${HF_REPORT}
"
  fi
else
  rec WARN hf-token "no token in $HF_ENV_FILE: gated base models can be neither trained nor evaluated; public repos download anonymously (rate limits)"
fi

# ---------------------------------------------------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------------------------------------------------
NFAIL="$(printf '%s' "$RESULTS" | grep -c '^FAIL' || true)"
NWARN="$(printf '%s' "$RESULTS" | grep -c '^WARN' || true)"
say "summary: FAIL=$NFAIL WARN=$NWARN dry=$DRY"
if [ "$DRY" != "1" ] && [ -d "$KIT_ROOT/logs" ]; then
  printf '%s' "$RESULTS" > "$KIT_ROOT/logs/provision_$TS.tsv" && say "report: $KIT_ROOT/logs/provision_$TS.tsv"
fi
if [ "$NFAIL" != "0" ]; then say "NOT READY - fix the FAIL lines above and re-run (idempotent)"; exit 1; fi
say "READY - next: from your workstation, bash stage.sh root@<box>  (README.md run order)"
exit 0
