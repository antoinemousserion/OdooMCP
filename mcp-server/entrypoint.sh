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

if [[ "${FORCE_RECLONE:-false}" == "true" ]]; then
    log "FORCE_RECLONE active - suppression du cache Git local..."
    rm -rf "${COMMUNITY_DIR}" "${ENTERPRISE_DIR}"
fi

clear_git_locks() {
    local dir="$1"
    if [[ ! -d "${dir}/.git" ]]; then
        return 0
    fi
    local lock
    for lock in index.lock shallow.lock HEAD.lock packed-refs.lock; do
        if [[ -f "${dir}/.git/${lock}" ]]; then
            log "Suppression lock Git stale : ${dir}/.git/${lock}"
            rm -f "${dir}/.git/${lock}"
        fi
    done
}

clone_or_update() {
    local dir="$1"
    local url="$2"
    local label="$3"

    clear_git_locks "${dir}"

    if [[ -d "${dir}/.git" ]]; then
        log "Mise à jour ${label} (${BRANCH})..."
        if ! git -C "${dir}" fetch --depth="${GIT_DEPTH}" origin "${BRANCH}" 2>/dev/null; then
            if ! git -C "${dir}" fetch origin "${BRANCH}"; then
                log "ATTENTION: git fetch ${label} echoue — code local conserve (verifiez index.lock si boucle de restart)"
                return 0
            fi
        fi
        git -C "${dir}" checkout "${BRANCH}" 2>/dev/null || git -C "${dir}" checkout -B "${BRANCH}" "origin/${BRANCH}" || {
            log "ATTENTION: git checkout ${label} echoue — code local conserve"
            return 0
        }
        git -C "${dir}" reset --hard "origin/${BRANCH}" || {
            log "ATTENTION: git reset ${label} echoue — code local conserve"
            return 0
        }
        return 0
    fi

    log "Clone initial ${label} (${BRANCH})..."
    mkdir -p "$(dirname "${dir}")"
    if [[ -d "${dir}" ]]; then
        rm -rf "${dir}"
    fi
    if [[ "${GIT_DEPTH}" != "0" && -n "${GIT_DEPTH}" ]]; then
        git clone --branch "${BRANCH}" --single-branch --depth "${GIT_DEPTH}" "${url}" "${dir}" || return 1
    else
        git clone --branch "${BRANCH}" --single-branch "${url}" "${dir}" || return 1
    fi
}

mkdir -p "${DATA_ROOT}"

# --- Community (public) ---
if ! clone_or_update "${COMMUNITY_DIR}" "${COMMUNITY_URL}" "community"; then
    log "ERREUR: clone community impossible. Verifiez le volume et supprimez .git/index.lock si present."
    if [[ ! -d "${COMMUNITY_DIR}/.git" ]]; then
        exit 1
    fi
    log "Community partiellement present — demarrage MCP avec le code disponible."
fi

# --- Enterprise (privé, optionnel) ---
if [[ "${CLONE_ENTERPRISE}" == "true" ]]; then
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        AUTH_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/odoo/enterprise.git"
        if ! clone_or_update "${ENTERPRISE_DIR}" "${AUTH_URL}" "enterprise"; then
            log "ATTENTION: clone enterprise echoue."
            log "  1. Connecte sur https://github.com/odoo/enterprise (404 = pas d'acces au repo)"
            log "  2. Liez votre compte GitHub sur https://www.odoo.com/my/home (partenaire/client Enterprise)"
            log "  3. PAT classique avec scope 'repo', ou PAT fine-grained avec acces odoo/enterprise"
            log "  4. Regenerer le token si expire, puis redemarrer le conteneur"
            log "  Community seul reste disponible pour le MCP."
        fi
    else
        log "GITHUB_TOKEN absent — enterprise non clone (community seul)"
    fi
else
    log "CLONE_ENTERPRISE=false — enterprise ignore"
fi

export ODOO_DATA_DIR="${DATA_DIR}"
export ODOO_COMMUNITY_DIR="${COMMUNITY_DIR}"
export ODOO_ENTERPRISE_DIR="${ENTERPRISE_DIR}"

log "Codebase prête. Démarrage MCP (transport=${MCP_TRANSPORT:-sse}, port=${MCP_PORT:-8000})..."
exec python -m odoo_mcp.server
