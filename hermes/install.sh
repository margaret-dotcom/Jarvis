#!/usr/bin/env bash
# Install the Jarvis layer into Hermes Agent. Safe to rerun.
#
#   bash hermes/install.sh                 install or update everything
#   bash hermes/install.sh --skip-cron     skills, SOUL.md and config only (works without hermes)
#   bash hermes/install.sh --profile jarvis   target the "jarvis" Hermes profile instead of the default
#   bash hermes/install.sh --yes           replace a customized SOUL.md without asking
#   bash hermes/install.sh --uninstall     remove the symlinks and the cron jobs by name
#
# What it does: symlinks every skill in hermes/skills/ (and hermes/skills/agents/)
# into the Hermes skills directory, installs SOUL.md if the current one is the
# Hermes default, merges hermes/config.snippet.yaml into config.yaml, and
# registers the jobs in hermes/cron/jobs.yaml with `hermes cron create`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALLER_URL="https://hermes-agent.nousresearch.com/install.sh"

UNINSTALL=0
SKIP_CRON=0
ASSUME_YES=0
PROFILE=""

usage() { sed -n '2,13p' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) UNINSTALL=1 ;;
    --skip-cron) SKIP_CRON=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --profile) shift; PROFILE="${1:-}"; [ -n "$PROFILE" ] || { echo "--profile needs a name"; exit 2; } ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1"; usage; exit 2 ;;
  esac
  shift
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

# ---------------------------------------------------------------- .env values
env_value() {
  # env_value KEY DEFAULT: read KEY from the repo .env, strip quotes, fall back.
  local key="$1" default="$2" line
  if [ -f "$REPO_DIR/.env" ]; then
    line="$(grep -E "^${key}=" "$REPO_DIR/.env" | tail -n1 | cut -d= -f2- || true)"
    line="${line%\"}"; line="${line#\"}"; line="${line%\'}"; line="${line#\'}"
    if [ -n "$line" ]; then printf '%s' "$line"; return; fi
  fi
  printf '%s' "$default"
}

JARVIS_HOME="$(env_value JARVIS_HOME "$REPO_DIR")"
JARVIS_TZ="$(env_value JARVIS_TZ "America/Chicago")"

if [ ! -f "$JARVIS_HOME/dashboard/data/schema.json" ]; then
  say "JARVIS_HOME=$JARVIS_HOME does not look like the Jarvis repo (no dashboard/data/schema.json)."
  say "Using this checkout instead: $REPO_DIR"
  JARVIS_HOME="$REPO_DIR"
fi

# ---------------------------------------------------------------- hermes home
HERMES_BASE="${HERMES_HOME:-$HOME/.hermes}"
if [ -n "$PROFILE" ]; then
  HERMES_DIR="$HERMES_BASE/profiles/$PROFILE"
  HERMES_CMD=(hermes -p "$PROFILE")
else
  HERMES_DIR="$HERMES_BASE"
  HERMES_CMD=(hermes)
fi
SKILLS_DIR="$HERMES_DIR/skills"

HAVE_HERMES=0
if command -v hermes >/dev/null 2>&1; then HAVE_HERMES=1; fi

if [ "$HAVE_HERMES" -eq 0 ] && [ "$SKIP_CRON" -eq 0 ] && [ "$UNINSTALL" -eq 0 ]; then
  say "hermes is not on PATH. Install Hermes Agent first:"
  say "  curl -fsSL $INSTALLER_URL | bash"
  say "Then rerun this script. To install only the skills, SOUL.md and config now: bash hermes/install.sh --skip-cron"
  exit 1
fi

if [ -n "$PROFILE" ] && [ ! -d "$HERMES_DIR" ]; then
  say "Profile '$PROFILE' does not exist at $HERMES_DIR."
  say "Create it first: hermes profile create $PROFILE --clone"
  exit 1
fi

say "Repo:        $JARVIS_HOME"
say "Timezone:    $JARVIS_TZ"
say "Hermes home: $HERMES_DIR"

# ---------------------------------------------------------------- skills
skill_dirs() {
  local d
  for d in "$REPO_DIR"/hermes/skills/*/ "$REPO_DIR"/hermes/skills/agents/*/; do
    d="${d%/}"
    [ -f "$d/SKILL.md" ] || continue
    printf '%s\n' "$d"
  done
}

link_skill() {
  local src="$1" name dst current
  name="$(basename "$src")"
  dst="$SKILLS_DIR/$name"
  if [ -L "$dst" ]; then
    current="$(readlink "$dst")"
    if [ "$current" = "$src" ]; then say "  ok      $name"; else say "  skip    $name (link points to $current)"; fi
  elif [ -e "$dst" ]; then
    say "  skip    $name (a real directory exists at $dst; move it aside to use the repo copy)"
  else
    ln -s "$src" "$dst"
    say "  linked  $name"
  fi
}

unlink_skill() {
  local src="$1" name dst current
  name="$(basename "$src")"
  dst="$SKILLS_DIR/$name"
  if [ -L "$dst" ]; then
    current="$(readlink "$dst")"
    case "$current" in
      "$REPO_DIR"/hermes/skills/*) rm "$dst"; say "  removed $name" ;;
      *) say "  kept    $name (link points elsewhere: $current)" ;;
    esac
  elif [ -e "$dst" ]; then
    say "  kept    $name (not a symlink)"
  fi
}

# ---------------------------------------------------------------- cron helpers
cron_list_names() {
  # Prints one job name per line from `hermes cron list --all`. Colors are off when piped.
  "${HERMES_CMD[@]}" cron list --all 2>/dev/null | sed -n 's/^ *Name: *//p' || true
}

cron_list_ids_and_names() {
  # Prints "<job_id><TAB><name>" lines.
  "${HERMES_CMD[@]}" cron list --all 2>/dev/null | python3 -c '
import re, sys
current = None
for line in sys.stdin:
    m = re.match(r"^  (\S+)\s", line)
    if m:
        current = m.group(1)
        continue
    m = re.match(r"^\s+Name:\s+(.*?)\s*$", line)
    if m and current:
        print(current + "\t" + m.group(1))
' || true
}

jobs_from_yaml() {
  # Prints one job per line: name TAB schedule TAB deliver TAB workdir TAB skills(comma) TAB prompt
  JARVIS_HOME="$JARVIS_HOME" python3 - "$REPO_DIR/hermes/cron/jobs.yaml" <<'PY'
import os, sys
try:
    import yaml
except ImportError:
    sys.exit("pyyaml is missing. Run: pip install -r requirements.txt")
doc = yaml.safe_load(open(sys.argv[1])) or {}
defaults = doc.get("defaults") or {}
home = os.environ["JARVIS_HOME"]
for job in doc.get("jobs") or []:
    name = str(job["name"]).strip()
    schedule = str(job["schedule"]).strip()
    deliver = str(job.get("deliver") or defaults.get("deliver") or "whatsapp")
    workdir = str(job.get("workdir") or defaults.get("workdir") or home).replace("$JARVIS_HOME", home)
    skills = ",".join(str(s) for s in (job.get("skills") or []))
    prompt = " ".join(str(job.get("prompt") or "").split())
    print("\t".join([name, schedule, deliver, workdir, skills, prompt]))
PY
}

# ================================================================ uninstall
if [ "$UNINSTALL" -eq 1 ]; then
  step "Removing skill symlinks from $SKILLS_DIR"
  if [ -d "$SKILLS_DIR" ]; then
    while IFS= read -r d; do unlink_skill "$d"; done < <(skill_dirs)
  else
    say "  no skills directory, nothing to remove"
  fi

  if [ "$SKIP_CRON" -eq 1 ] || [ "$HAVE_HERMES" -eq 0 ]; then
    step "Skipping cron removal (hermes not on PATH or --skip-cron)"
  else
    step "Removing cron jobs listed in hermes/cron/jobs.yaml"
    wanted="$(jobs_from_yaml | cut -f1)"
    found=0
    while IFS=$'\t' read -r job_id job_name; do
      [ -n "$job_id" ] || continue
      if grep -Fxq "$job_name" <<<"$wanted"; then
        "${HERMES_CMD[@]}" cron remove "$job_id" >/dev/null && say "  removed $job_name ($job_id)"
        found=1
      fi
    done < <(cron_list_ids_and_names)
    [ "$found" -eq 1 ] || say "  none of the Jarvis jobs are registered"
  fi

  say ""
  say "SOUL.md and config.yaml were left in place. Edit them by hand if you want them back to stock."
  exit 0
fi

# ================================================================ install
step "Linking skills into $SKILLS_DIR"
mkdir -p "$SKILLS_DIR"
while IFS= read -r d; do link_skill "$d"; done < <(skill_dirs)

# ---------------------------------------------------------------- SOUL.md
step "SOUL.md"
SOUL_SRC="$REPO_DIR/hermes/SOUL.md"
SOUL_DST="$HERMES_DIR/SOUL.md"
mkdir -p "$HERMES_DIR"

soul_is_stock() {
  # Exit 0 when the existing SOUL.md is the Hermes default or a legacy empty template.
  python3 - "$1" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8-sig").read().replace("\r\n", "\n").strip()
default = ("You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
           "You are helpful, knowledgeable, and direct. You assist users with a wide "
           "range of tasks including answering questions, writing and editing code, "
           "analyzing information, creative work, and executing actions via your tools. "
           "You communicate clearly, admit uncertainty when appropriate, and prioritize "
           "being genuinely useful over being verbose unless otherwise directed below. "
           "Be targeted and efficient in your exploration and investigations.")
if not text or " ".join(text.split()) == " ".join(default.split()):
    sys.exit(0)
if text.startswith("# Hermes Agent Persona"):
    body = re.sub(r"<!--.*?-->", "", text[len("# Hermes Agent Persona"):], flags=re.S).strip()
    sys.exit(0 if not body else 1)
sys.exit(1)
PY
}

if [ ! -f "$SOUL_DST" ]; then
  cp "$SOUL_SRC" "$SOUL_DST"; say "  installed (none existed)"
elif cmp -s "$SOUL_SRC" "$SOUL_DST"; then
  say "  ok (already the Jarvis persona)"
elif soul_is_stock "$SOUL_DST"; then
  cp "$SOUL_DST" "$SOUL_DST.bak.$(date +%Y%m%d-%H%M%S)"
  cp "$SOUL_SRC" "$SOUL_DST"; say "  replaced the stock Hermes persona (backup saved next to it)"
else
  say "  $SOUL_DST differs from hermes/SOUL.md:"
  diff -u "$SOUL_DST" "$SOUL_SRC" | head -n 80 || true
  replace=0
  if [ "$ASSUME_YES" -eq 1 ]; then
    replace=1
  elif [ -t 0 ]; then
    printf '  Replace it with the Jarvis persona? A backup is kept. [y/N] '
    read -r answer
    case "$answer" in y|Y|yes|YES) replace=1 ;; esac
  else
    say "  non-interactive run: keeping the existing SOUL.md (pass --yes to replace)"
  fi
  if [ "$replace" -eq 1 ]; then
    cp "$SOUL_DST" "$SOUL_DST.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$SOUL_SRC" "$SOUL_DST"; say "  replaced (backup saved next to it)"
  else
    say "  kept the existing SOUL.md"
  fi
fi

# ---------------------------------------------------------------- config.yaml
step "Merging hermes/config.snippet.yaml into $HERMES_DIR/config.yaml"
JARVIS_TZ="$JARVIS_TZ" python3 - "$REPO_DIR/hermes/config.snippet.yaml" "$HERMES_DIR/config.yaml" <<'PY'
import os, shutil, sys, time
try:
    import yaml
except ImportError:
    sys.exit("  pyyaml is missing. Run: pip install -r requirements.txt, then rerun install.sh")

snippet_path, config_path = sys.argv[1], sys.argv[2]
snippet = yaml.safe_load(open(snippet_path)) or {}
if "timezone" in snippet:
    snippet["timezone"] = os.environ.get("JARVIS_TZ") or snippet["timezone"]

existing = {}
if os.path.exists(config_path):
    existing = yaml.safe_load(open(config_path)) or {}
    if not isinstance(existing, dict):
        sys.exit(f"  {config_path} is not a mapping; fix it by hand before merging")

changes = []
def merge(dst, src, path=""):
    for key, value in src.items():
        here = f"{path}.{key}" if path else key
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            merge(dst[key], value, here)
        elif isinstance(value, dict) and key not in dst:
            dst[key] = {}
            merge(dst[key], value, here)
        else:
            if dst.get(key, object()) != value:
                changes.append(f"{here}: {dst.get(key, '(unset)')!r} -> {value!r}")
                dst[key] = value

merge(existing, snippet)
if not changes:
    print("  ok (nothing to change)")
    sys.exit(0)
if os.path.exists(config_path):
    backup = f"{config_path}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(config_path, backup)
    print(f"  backup: {backup}")
os.makedirs(os.path.dirname(config_path), exist_ok=True)
tmp = config_path + ".tmp"
with open(tmp, "w") as fh:
    yaml.safe_dump(existing, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)
os.replace(tmp, config_path)
for c in changes:
    print("  " + c)
PY

# ---------------------------------------------------------------- cron
if [ "$SKIP_CRON" -eq 1 ] || [ "$HAVE_HERMES" -eq 0 ]; then
  step "Skipping cron registration (hermes not on PATH or --skip-cron)"
else
  step "Registering cron jobs from hermes/cron/jobs.yaml"
  existing_names="$(cron_list_names)"
  while IFS=$'\t' read -r name schedule deliver workdir skills prompt; do
    [ -n "$name" ] || continue
    if grep -Fxq "$name" <<<"$existing_names"; then
      say "  exists  $name"
      continue
    fi
    args=(cron create "$schedule" "$prompt" --name "$name" --deliver "$deliver" --workdir "$workdir")
    if [ -n "$skills" ]; then
      IFS=',' read -r -a skill_list <<<"$skills"
      for s in "${skill_list[@]}"; do args+=(--skill "$s"); done
    fi
    if "${HERMES_CMD[@]}" "${args[@]}" >/dev/null; then
      say "  created $name  ($schedule, deliver $deliver)"
    else
      say "  FAILED  $name (run the command by hand to see why: hermes cron create \"$schedule\" ... --name $name)"
    fi
  done < <(jobs_from_yaml)
fi

# ---------------------------------------------------------------- next steps
step "Next steps"
p=""; [ -n "$PROFILE" ] && p="-p $PROFILE "
say "  1. Pick the model Jarvis runs on:        hermes ${p}model"
say "  2. Pair WhatsApp for deliveries:          hermes ${p}whatsapp   (scan the QR code with your phone; briefs land in your own chat)"
if ! command -v node >/dev/null 2>&1; then
  say "     Node.js is not on PATH. The WhatsApp bridge needs it. Install Node.js before step 2."
fi
say "  3. Connect Google accounts for the collectors:  cd $JARVIS_HOME && python -m collectors.google_auth"
say "  4. Put SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and DOMOS_OWNER_EMAIL in $JARVIS_HOME/.env so the collectors can read DOM OS"
say "  5. Fill goals/goals.yaml, then try a job:  hermes ${p}cron list   and   hermes ${p}cron run <job_id>"
say "  6. Keep the gateway running so cron fires: hermes ${p}gateway install"
say ""
say "Skills are symlinked, so edits under $REPO_DIR/hermes/skills apply live. Commit them weekly."
