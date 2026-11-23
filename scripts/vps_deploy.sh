#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

DEPLOY_DIR="${TA_DEPLOY_DIR:-/opt/tradingagents}"
DATA_DIR="${TA_DATA_DIR:-/var/lib/tradingagents}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DEPLOY_DIR/.env"
RELEASE_FILE="$DEPLOY_DIR/release.env"
PREVIOUS_RELEASE_FILE="$DEPLOY_DIR/previous-release.env"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.split.yml"
BACKUP_SCRIPT="${TA_BACKUP_SCRIPT:-$SCRIPT_DIR/vps_backup.py}"
SMOKE_SCRIPT="${TA_SMOKE_SCRIPT:-$SCRIPT_DIR/smoke.py}"
SMOKE_BASE_URL="${TA_SMOKE_BASE_URL:-http://127.0.0.1:8000}"
SMOKE_TIMEOUT="${TA_SMOKE_TIMEOUT:-120}"
LOCK_FILE="${TA_DEPLOY_LOCK_FILE:-$DEPLOY_DIR/.deploy.lock}"
DEPLOY_ACTIVE=0
RELEASE_TEMP=""

log() {
    printf '[vps-deploy] %s\n' "$*"
}

usage() {
    printf 'Usage: %s deploy <image-ref> | rollback\n' "$0" >&2
}

cleanup() {
    if [[ -n "$RELEASE_TEMP" ]]; then
        rm -f -- "$RELEASE_TEMP" || true
    fi
}
trap cleanup EXIT

validate_image_ref() {
    local image_ref="$1"
    local digest_pattern='^[A-Za-z0-9][-A-Za-z0-9._/:]*@sha256:[0-9a-f]{64}$'
    if [[ -z "$image_ref" || ${#image_ref} -gt 512 || ! $image_ref =~ $digest_pattern ]]; then
        printf 'Invalid image reference; expected repository@sha256:<64 lowercase hex characters>.\n' >&2
        return 1
    fi
}

read_image_ref() {
    local file="$1"
    local line value first last
    [[ -r "$file" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        if [[ $line =~ ^[[:space:]]*(export[[:space:]]+)?IMAGE_REF[[:space:]]*=(.*)$ ]]; then
            value="${BASH_REMATCH[2]}"
            value="${value#"${value%%[![:space:]]*}"}"
            value="${value%"${value##*[![:space:]]}"}"
            first="${value:0:1}"
            last="${value: -1}"
            if [[ ${#value} -ge 2 && "$first" == '"' && "$last" == '"' ]]; then
                value="${value:1:${#value}-2}"
            elif [[ ${#value} -ge 2 && "$first" == "'" && "$last" == "'" ]]; then
                value="${value:1:${#value}-2}"
            fi
            validate_image_ref "$value" || return 1
            printf '%s\n' "$value"
            return 0
        fi
    done < "$file"
    return 1
}

write_release() {
    local image_ref="$1"
    RELEASE_TEMP="$(mktemp "$DEPLOY_DIR/.release.env.XXXXXX")"
    printf 'IMAGE_REF=%s\n' "$image_ref" > "$RELEASE_TEMP"
    mv -f -- "$RELEASE_TEMP" "$RELEASE_FILE"
    RELEASE_TEMP=""
}

preserve_previous_release() {
    [[ -f "$RELEASE_FILE" ]] || return 0
    read_image_ref "$RELEASE_FILE" > /dev/null
    RELEASE_TEMP="$(mktemp "$DEPLOY_DIR/.previous-release.env.XXXXXX")"
    cp -- "$RELEASE_FILE" "$RELEASE_TEMP"
    mv -f -- "$RELEASE_TEMP" "$PREVIOUS_RELEASE_FILE"
    RELEASE_TEMP=""
}

compose() {
    docker compose --env-file "$ENV_FILE" --env-file "$RELEASE_FILE" -f "$COMPOSE_FILE" "$@"
}

show_recent_logs() {
    if [[ -r "$ENV_FILE" && -r "$RELEASE_FILE" && -r "$COMPOSE_FILE" ]]; then
        log 'Recent service logs:'
        compose logs --tail=120 migrate redis api scheduler || true
    fi
}

apply_release() {
    local image_ref="$1"
    log "Pulling $image_ref"
    compose pull migrate redis api scheduler || return $?
    log 'Starting migrate, redis, api, and scheduler'
    compose up -d --remove-orphans migrate redis api scheduler || return $?
    python3 "$SMOKE_SCRIPT" --base-url "$SMOKE_BASE_URL" --timeout "$SMOKE_TIMEOUT" || return $?
}

backup_database() {
    python3 "$BACKUP_SCRIPT" \
        --source "$DATA_DIR/data/tradingagents.db" \
        --backup-dir "$DATA_DIR/backups"
}

handle_error() {
    local status="$1"
    local line="$2"
    local previous_image=""
    trap - ERR
    set +e
    if [[ "$status" -eq 0 ]]; then
        status=1
    fi
    log "Deployment command failed at line $line (exit $status)."
    show_recent_logs

    if [[ "$DEPLOY_ACTIVE" -eq 1 && -r "$PREVIOUS_RELEASE_FILE" ]]; then
        DEPLOY_ACTIVE=0
        previous_image="$(read_image_ref "$PREVIOUS_RELEASE_FILE")"
        if [[ -n "$previous_image" ]]; then
            log "Attempting automatic rollback to $previous_image; the database will not be restored."
            if write_release "$previous_image" && apply_release "$previous_image"; then
                log 'Automatic rollback passed smoke checks; the original deployment still failed.'
            else
                log 'Automatic rollback failed.'
                show_recent_logs
            fi
        else
            log 'previous-release.env has no valid IMAGE_REF; automatic rollback was not possible.'
        fi
    fi
    exit "$status"
}
trap 'handle_error $? $LINENO' ERR

if [[ $# -lt 1 ]]; then
    usage
    exit 64
fi

COMMAND="$1"
case "$COMMAND" in
    deploy)
        if [[ $# -ne 2 ]]; then
            usage
            exit 64
        fi
        IMAGE_REF="$2"
        validate_image_ref "$IMAGE_REF"
        ;;
    rollback)
        if [[ $# -ne 1 ]]; then
            usage
            exit 64
        fi
        ;;
    *)
        usage
        exit 64
        ;;
esac

for executable in docker python3 flock; do
    if ! command -v "$executable" > /dev/null 2>&1; then
        printf 'Required executable not found: %s\n' "$executable" >&2
        exit 69
    fi
done

if [[ ! -d "$DEPLOY_DIR" ]]; then
    printf 'Deployment directory does not exist: %s\n' "$DEPLOY_DIR" >&2
    exit 66
fi

exec 9> "$LOCK_FILE"
if ! flock -n 9; then
    printf 'Another deployment or rollback is already running.\n' >&2
    exit 75
fi

for required_file in "$ENV_FILE" "$COMPOSE_FILE" "$BACKUP_SCRIPT" "$SMOKE_SCRIPT"; do
    if [[ ! -r "$required_file" ]]; then
        printf 'Required file is missing or unreadable: %s\n' "$required_file" >&2
        exit 66
    fi
done

docker compose version > /dev/null

case "$COMMAND" in
    deploy)
        backup_database
        preserve_previous_release
        write_release "$IMAGE_REF"
        DEPLOY_ACTIVE=1
        apply_release "$IMAGE_REF"
        DEPLOY_ACTIVE=0
        log "Deployment of $IMAGE_REF completed successfully."
        ;;
    rollback)
        if [[ ! -r "$PREVIOUS_RELEASE_FILE" ]]; then
            printf 'No previous release is available: %s\n' "$PREVIOUS_RELEASE_FILE" >&2
            exit 66
        fi
        IMAGE_REF="$(read_image_ref "$PREVIOUS_RELEASE_FILE")"
        write_release "$IMAGE_REF"
        log "Rolling back to $IMAGE_REF; the database will not be restored."
        apply_release "$IMAGE_REF"
        log "Rollback to $IMAGE_REF completed successfully."
        ;;
esac
