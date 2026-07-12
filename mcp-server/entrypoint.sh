#!/bin/bash
set -euo pipefail

VERSION="${ODOO_VERSION:?ODOO_VERSION requis}"
BRANCH="${ODOO_BRANCH:-${VERSION}.0}"
DATA_ROOT="${ODOO_DATA_ROOT:-/data/odoo}"
DATA_DIR="${DATA_ROOT}/v${VERSION}"
COMMUNITY_DIR="${DATA_DIR}/community"
ENTERPRISE_DIR="${DATA_DIR}/enterprise"
GIT_DEPTH="${GIT_DEPTH:-1}"
CLONE_ENTERPRISE="${CLONE_ENTERPRISE:-true}"

COMMUNITY_URL="${ODOO_COMMUNITY_URL:-https://github.com/odoo/odoo.git}"
ENTERPRISE_URL="${ODOO_ENTERPRISE_URL:-https://github.com/odoo/enterprise.git}"

log() { echo "[odoo-mcp v${VERSION}] $*"; }

clone_or_update() {
    local dir="$1"
    local url="$2"
    local label="$3"

    if [[ -d "${dir}/.git" ]]; then
        log "Mise à jour ${label} (${BRANCH})..."
        git -C "${dir}" fetch --depth="${GIT_DEPTH}" origin "${BRANCH}" 2>/dev/null || \
            git -C "${dir}" fetch origin "${BRANCH}"
        git -C "${dir}" checkout "${BRANCH}" 2>/dev/null || git -C "${dir}" checkout -B "${BRANCH}" "origin/${BRANCH}"
        git -C "${dir}" reset --hard "origin/${BRANCH}"
    else
        log "Clone initial ${label} (${BRANCH})..."
        mkdir -p "$(dirname "${dir}")"
        if [[ "${GIT_DEPTH}" != "0" && -n "${GIT_DEPTH}" ]]; then
            git clone --branch "${BRANCH}" --single-branch --depth "${GIT_DEPTH}" "${url}" "${dir}"
        else
            git clone --branch "${BRANCH}" --single-branch "${url}" "${dir}"
        fi
    fi
}

mkdir -p "${DATA_ROOT}"

# --- Community (public) ---
clone_or_update "${COMMUNITY_DIR}" "${COMMUNITY_URL}" "community"

# --- Enterprise (privé, optionnel) ---
if [[ "${CLONE_ENTERPRISE}" == "true" ]]; then
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        AUTH_URL="https://${GITHUB_TOKEN}@github.com/odoo/enterprise.git"
        clone_or_update "${ENTERPRISE_DIR}" "${AUTH_URL}" "enterprise" || \
            log "ATTENTION: clone enterprise échoué — vérifiez GITHUB_TOKEN et accès odoo/enterprise"
    else
        log "GITHUB_TOKEN absent — enterprise non cloné (community seul)"
    fi
else
    log "CLONE_ENTERPRISE=false — enterprise ignoré"
fi

export ODOO_DATA_DIR="${DATA_DIR}"
export ODOO_COMMUNITY_DIR="${COMMUNITY_DIR}"
export ODOO_ENTERPRISE_DIR="${ENTERPRISE_DIR}"

log "Codebase prête. Démarrage MCP (transport=${MCP_TRANSPORT:-sse}, port=${MCP_PORT:-8000})..."
exec python -m odoo_mcp.server
