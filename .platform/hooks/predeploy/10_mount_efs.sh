#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_TOOL="/opt/elasticbeanstalk/bin/get-config"

get_environment_property() {
    local property_name="$1"
    local property_value="${!property_name:-}"

    if [[ -z "${property_value}" && -x "${CONFIG_TOOL}" ]]; then
        property_value="$("${CONFIG_TOOL}" environment -k "${property_name}" 2>/dev/null || true)"
    fi

    printf '%s' "${property_value}"
}

EFS_FILE_SYSTEM_ID="$(get_environment_property EFS_FILE_SYSTEM_ID)"
EFS_MOUNT_DIRECTORY="$(get_environment_property EFS_MOUNT_DIRECTORY)"

if [[ ! "${EFS_FILE_SYSTEM_ID}" =~ ^fs-[0-9a-f]+$ ]]; then
    echo "EFS_FILE_SYSTEM_ID 未設定或格式錯誤：${EFS_FILE_SYSTEM_ID:-<empty>}" >&2
    exit 1
fi

if [[ ! "${EFS_MOUNT_DIRECTORY}" =~ ^/mnt/efs(/[A-Za-z0-9._-]+)*$ ]]; then
    echo "EFS_MOUNT_DIRECTORY 必須位於 /mnt/efs：${EFS_MOUNT_DIRECTORY:-<empty>}" >&2
    exit 1
fi

if ! command -v mount.efs >/dev/null 2>&1; then
    echo "Installing amazon-efs-utils..."
    dnf -y install amazon-efs-utils
fi

mkdir -p "${EFS_MOUNT_DIRECTORY}"

FSTAB_ENTRY="${EFS_FILE_SYSTEM_ID}:/ ${EFS_MOUNT_DIRECTORY} efs _netdev,tls 0 0"
if ! grep -Fqx "${FSTAB_ENTRY}" /etc/fstab; then
    sed -i "\|^[^#].*[[:space:]]${EFS_MOUNT_DIRECTORY}[[:space:]]|d" /etc/fstab
    printf '%s\n' "${FSTAB_ENTRY}" >> /etc/fstab
fi

if ! mountpoint -q "${EFS_MOUNT_DIRECTORY}"; then
    echo "Mounting ${EFS_FILE_SYSTEM_ID} at ${EFS_MOUNT_DIRECTORY} with TLS..."
    mount -t efs -o tls "${EFS_FILE_SYSTEM_ID}:/" "${EFS_MOUNT_DIRECTORY}"
fi

if ! mountpoint -q "${EFS_MOUNT_DIRECTORY}"; then
    echo "EFS 掛載失敗：${EFS_MOUNT_DIRECTORY} 不是掛載點" >&2
    exit 1
fi

PERSISTENT_DIRECTORIES=(
    "${EFS_MOUNT_DIRECTORY}/uploads"
    "${EFS_MOUNT_DIRECTORY}/case_uploads"
    "${EFS_MOUNT_DIRECTORY}/data/processed/json_web_uploads"
    "${EFS_MOUNT_DIRECTORY}/data/processed/rag_index"
)

install -d -m 2775 "${EFS_MOUNT_DIRECTORY}" "${PERSISTENT_DIRECTORIES[@]}"

if id webapp >/dev/null 2>&1; then
    chown webapp:webapp "${EFS_MOUNT_DIRECTORY}" "${PERSISTENT_DIRECTORIES[@]}"
else
    echo "找不到 Elastic Beanstalk 的 webapp 使用者，無法設定 EFS 寫入權限" >&2
    exit 1
fi

echo "EFS ready: ${EFS_FILE_SYSTEM_ID} -> ${EFS_MOUNT_DIRECTORY}"
