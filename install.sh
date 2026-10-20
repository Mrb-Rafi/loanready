#!/usr/bin/env bash
# LoanReady one-command installer for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/Mrb-Rafi/loanready/main/install.sh | bash
#
# What it does (nothing needs root):
#   1. finds Python 3.12 (stops with the exact install command if it is missing)
#   2. downloads LoanReady into ~/loanready (git clone, or the GitHub ZIP if git is absent);
#      re-running it updates an existing copy
#   3. creates LoanReady's own Python environment (.venv) and installs its packages
#   4. creates .env from .env.example, loads the demo data (Rina's Bakery)
#   5. adds a `loanready` command (~/.local/bin) and, on Linux, an app-menu entry
#
# Options (environment variables):
#   LOANREADY_DIR=/path      install somewhere else (default: ~/loanready)
#   LOANREADY_NO_START=1     do not start the app at the end
set -euo pipefail

# Wrapped in main() so `curl ... | bash` reads the whole script before running any of it.
main() {

REPO="https://github.com/Mrb-Rafi/loanready"
BRANCH="main"
DIR="${LOANREADY_DIR:-$HOME/loanready}"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

case "$DIR" in *" "*) fail "install path must not contain spaces: $DIR";; esac

# 1. Python 3.12 -------------------------------------------------------------
PY=""
for cand in python3.12 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 &&
     "$cand" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
if [ -z "$PY" ]; then
  echo
  echo "LoanReady needs Python 3.12 exactly. Install it, then run this installer again:"
  if command -v apt-get >/dev/null 2>&1; then
    echo "  sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv"
    echo "  (Ubuntu 22.04 or older: sudo add-apt-repository ppa:deadsnakes/ppa first)"
  elif command -v dnf >/dev/null 2>&1; then
    echo "  sudo dnf install -y python3.12"
  elif command -v pacman >/dev/null 2>&1; then
    echo "  Arch ships a newer Python; use pyenv: pyenv install 3.12 && pyenv shell 3.12"
  elif command -v brew >/dev/null 2>&1; then
    echo "  brew install python@3.12"
  else
    echo "  https://www.python.org/downloads/ (choose a 3.12.x release)"
  fi
  exit 1
fi
if ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  fail "Python 3.12 is missing its venv module. Debian/Ubuntu/Mint: sudo apt-get install -y python3.12-venv"
fi
say "Python: $PY ($("$PY" --version))"

# 2. Download or update -------------------------------------------------------
if [ -d "$DIR/.git" ]; then
  say "Updating existing copy in $DIR"
  git -C "$DIR" pull --ff-only
elif [ -f "$DIR/tasks.py" ]; then
  say "Using existing copy in $DIR (not a git checkout; not updated)"
elif command -v git >/dev/null 2>&1; then
  say "Downloading LoanReady into $DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO.git" "$DIR"
else
  say "git not found; downloading the ZIP into $DIR"
  command -v curl >/dev/null 2>&1 || fail "need git or curl"
  tmp="$(mktemp -d)"
  curl -fsSL "$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$tmp"
  mkdir -p "$(dirname "$DIR")"
  mv "$tmp/loanready-$BRANCH" "$DIR"
  rmdir "$tmp"
fi
cd "$DIR"

# 3. Python environment and packages -----------------------------------------
say "Installing LoanReady's Python packages (2-5 minutes, about 620 MB)"
"$PY" tasks.py setup

# 4. Settings and demo data ----------------------------------------------------
[ -f .env ] || { cp .env.example .env; say "Created .env (defaults: mock AI, built-in simulator)"; }
# Streamlit asks for an e-mail on its first start; an empty credentials file skips that.
if [ ! -f "$HOME/.streamlit/credentials.toml" ]; then
  mkdir -p "$HOME/.streamlit"
  printf '[general]\nemail = ""\n' > "$HOME/.streamlit/credentials.toml"
fi
say "Checking the installation"
.venv/bin/python tasks.py doctor
say "Loading the demo data (about 30 seconds)"
.venv/bin/python tasks.py seed

# 5. Launcher -------------------------------------------------------------------
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
cat > "$BIN/loanready" <<EOF
#!/usr/bin/env bash
# Start LoanReady (installed in $DIR). Open http://127.0.0.1:8501 ; Ctrl+C stops it.
cd "$DIR" && exec .venv/bin/python tasks.py "\${@:-run}"
EOF
chmod +x "$BIN/loanready"
if [ "$(uname -s)" = "Linux" ]; then
  APPS="$HOME/.local/share/applications"
  mkdir -p "$APPS"
  cat > "$APPS/loanready.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LoanReady
Comment=SME bookkeeping and loan-readiness agent (sandbox demo)
Exec=$BIN/loanready run
Terminal=true
Categories=Office;Finance;
EOF
fi

echo
say "LoanReady is installed in $DIR"
echo "    Start it any time with:  loanready"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
      if [ -f "$rc" ] && ! grep -q 'LoanReady launcher' "$rc"; then
        printf '\n# LoanReady launcher\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
      fi
    done
    echo "    (open a new terminal first, or run $BIN/loanready)";;
esac
echo "    Then open http://127.0.0.1:8501 in your browser. Ctrl+C in the terminal stops it."
echo

if [ "${LOANREADY_NO_START:-0}" != "1" ]; then
  say "Starting LoanReady now"
  exec .venv/bin/python tasks.py ui
fi
}

main "$@"
