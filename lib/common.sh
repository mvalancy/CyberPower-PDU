# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

# Shared shell library — sourced by all scripts.

# --- Colors (auto-disabled when not a TTY) ---
if [ -t 1 ]; then
    _RED='\033[0;31m'
    _GREEN='\033[0;32m'
    _YELLOW='\033[0;33m'
    _BLUE='\033[0;34m'
    _CYAN='\033[0;36m'
    _BOLD='\033[1m'
    _RESET='\033[0m'
else
    _RED='' _GREEN='' _YELLOW='' _BLUE='' _CYAN='' _BOLD='' _RESET=''
fi

info()    { echo -e "${_BLUE}[info]${_RESET}  $*"; }
success() { echo -e "${_GREEN}[ok]${_RESET}    $*"; }
warn()    { echo -e "${_YELLOW}[warn]${_RESET}  $*"; }
error()   { echo -e "${_RED}[error]${_RESET} $*"; }
step()    { echo -e "${_CYAN}${_BOLD}==> $*${_RESET}"; }

# --- Banner ---
banner() {
    echo -e "${_BOLD}CyberPower PDU Bridge${_RESET}"
    echo "Created by Matthew Valancy, Valpatel Software LLC"
    echo "Copyright 2026 MIT License"
    echo "https://github.com/mvalancy/CyberPower-PDU"
    echo ""
}

# --- Help parser ---
# Usage: check_help "$1" "script-name" "Short description" "Usage details"
check_help() {
    local arg="${1:-}"
    local script="$2"
    local desc="$3"
    local usage="$4"

    if [ "$arg" = "-h" ] || [ "$arg" = "--help" ]; then
        banner
        echo -e "${_BOLD}$script${_RESET} — $desc"
        echo ""
        echo "$usage"
        exit 0
    fi
}
