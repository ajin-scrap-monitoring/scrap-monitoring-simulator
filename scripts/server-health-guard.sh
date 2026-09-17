#!/usr/bin/env bash
# Guards a server validation command with host health samples.
set -euo pipefail

sample_interval_s="${SCRAP_SIMULATOR_HEALTH_SAMPLE_INTERVAL_S:-1}"
preflight_duration_s="${SCRAP_SIMULATOR_HEALTH_PREFLIGHT_DURATION_S:-60}"
soak_duration_s="${SCRAP_SIMULATOR_HEALTH_SOAK_DURATION_S:-300}"
cooldown_duration_s="${SCRAP_SIMULATOR_HEALTH_COOLDOWN_DURATION_S:-60}"
temperature_preflight_c="${SCRAP_SIMULATOR_HEALTH_PREFLIGHT_TEMP_C:-75}"
temperature_warning_c="${SCRAP_SIMULATOR_HEALTH_WARNING_TEMP_C:-80}"
temperature_abort_c="${SCRAP_SIMULATOR_HEALTH_ABORT_TEMP_C:-90}"
temperature_sustained_abort_c="${SCRAP_SIMULATOR_HEALTH_SUSTAINED_TEMP_C:-85}"
temperature_required="${SCRAP_SIMULATOR_HEALTH_REQUIRE_TEMPERATURE:-true}"
load_preflight="${SCRAP_SIMULATOR_HEALTH_PREFLIGHT_LOAD:-2.0}"
load_abort="${SCRAP_SIMULATOR_HEALTH_ABORT_LOAD:-6.0}"
psi_preflight="${SCRAP_SIMULATOR_HEALTH_PREFLIGHT_CPU_PSI:-10.0}"
psi_warning="${SCRAP_SIMULATOR_HEALTH_WARNING_CPU_PSI:-25.0}"
psi_abort="${SCRAP_SIMULATOR_HEALTH_ABORT_CPU_PSI:-50.0}"
memory_preflight_mib="${SCRAP_SIMULATOR_HEALTH_PREFLIGHT_MEMORY_MIB:-6144}"
memory_abort_mib="${SCRAP_SIMULATOR_HEALTH_ABORT_MEMORY_MIB:-2048}"
swap_preflight_mib="${SCRAP_SIMULATOR_HEALTH_PREFLIGHT_SWAP_MIB:-256}"
swap_growth_abort_mib="${SCRAP_SIMULATOR_HEALTH_ABORT_SWAP_GROWTH_MIB:-512}"
containers=()
command=()
log_file=""
dry_run=false
command_pid=""
baseline_swap_mib=0
high_temperature_samples=0
high_load_samples=0
high_psi_samples=0
low_memory_samples=0
had_warning=false
abort_reason=""

usage() {
    cat <<'EOF'
usage: scripts/server-health-guard.sh [options] [-- command [arguments...]]

options:
  --container NAME             Check a validation target container.
  --log-file PATH              Write samples to PATH. Default: /tmp.
  --dry-run                    Run preflight and cooldown without a command.
  --preflight-seconds N        Default: 60.
  --soak-seconds N             Default: 300.
  --cooldown-seconds N         Default: 60.
  -h, --help                   Show this help.

The command must own only its validation process group. The guard never
stops unrelated containers or processes.
EOF
}

require_uint() {
    local value="$1"
    local name="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        echo "$name must be a non-negative integer" >&2
        exit 2
    fi
}

require_bool() {
    local value="$1"
    local name="$2"
    if [[ "$value" != true && "$value" != false ]]; then
        echo "$name must be true or false" >&2
        exit 2
    fi
}

while (($#)); do
    case "$1" in
        --container)
            containers+=("${2:?--container requires a name}")
            shift 2
            ;;
        --log-file)
            log_file="${2:?--log-file requires a path}"
            shift 2
            ;;
        --dry-run)
            dry_run=true
            shift
            ;;
        --preflight-seconds)
            preflight_duration_s="${2:?--preflight-seconds requires a value}"
            shift 2
            ;;
        --soak-seconds)
            soak_duration_s="${2:?--soak-seconds requires a value}"
            shift 2
            ;;
        --cooldown-seconds)
            cooldown_duration_s="${2:?--cooldown-seconds requires a value}"
            shift 2
            ;;
        --)
            shift
            command=("$@")
            break
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for duration in "$sample_interval_s" "$preflight_duration_s" "$soak_duration_s" "$cooldown_duration_s"; do
    require_uint "$duration" "duration"
done
require_bool "$temperature_required" "SCRAP_SIMULATOR_HEALTH_REQUIRE_TEMPERATURE"

if [[ -z "$log_file" ]]; then
    log_file="$(mktemp /tmp/scrap-simulator-health.XXXXXX.log)"
fi
mkdir -p "$(dirname "$log_file")"
touch "$log_file"

if ! "$dry_run" && ((${#command[@]} == 0)); then
    echo "a guarded acceptance command is required unless --dry-run is used" >&2
    exit 2
fi

float_compare() {
    local expression="$1"
    awk "BEGIN { exit !($expression) }"
}

read_temperature_c() {
    local hwmon name input max_millicelsius=0 value
    shopt -s nullglob
    for hwmon in /sys/class/hwmon/hwmon*; do
        [[ -r "$hwmon/name" ]] || continue
        name="$(<"$hwmon/name")"
        case "$name" in
            coretemp|k10temp)
                for input in "$hwmon"/temp*_input; do
                    [[ -r "$input" ]] || continue
                    value="$(<"$input")"
                    [[ "$value" =~ ^[0-9]+$ ]] || continue
                    ((value > max_millicelsius)) && max_millicelsius="$value"
                done
                ;;
        esac
    done
    shopt -u nullglob
    if ((max_millicelsius > 0)); then
        awk -v value="$max_millicelsius" 'BEGIN { printf "%.1f", value / 1000 }'
    else
        printf '%s' "unavailable"
    fi
}

read_cpu_psi() {
    local line
    line="$(grep '^some ' /proc/pressure/cpu 2>/dev/null || true)"
    if [[ "$line" =~ avg10=([0-9.]+) ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    else
        printf '%s' "unavailable"
    fi
}

read_meminfo_mib() {
    local field="$1"
    local kib
    kib="$(awk -v field="$field" '$1 == field ":" { print $2; exit }' /proc/meminfo)"
    [[ "$kib" =~ ^[0-9]+$ ]] || kib=0
    printf '%s' "$((kib / 1024))"
}

read_swap_used_mib() {
    local total free
    total="$(read_meminfo_mib SwapTotal)"
    free="$(read_meminfo_mib SwapFree)"
    printf '%s' "$((total - free))"
}

container_failed() {
    local container state restarts baseline
    for container in "${containers[@]}"; do
        if ! docker inspect "$container" >/dev/null 2>&1; then
            abort_reason="container missing: $container"
            return 0
        fi
        state="$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}} {{.State.OOMKilled}} {{.RestartCount}}' "$container" 2>/dev/null || true)"
        if [[ "$state" == *" unhealthy "* || "$state" == *" true "* ]]; then
            abort_reason="container unhealthy or OOM: $container"
            return 0
        fi
        restarts="${state##* }"
        baseline="$(awk -v name="$container" '$1 == name { print $2; exit }' "$log_file.restarts" 2>/dev/null || true)"
        if [[ -n "$baseline" && "$restarts" =~ ^[0-9]+$ && "$restarts" -gt "$baseline" ]]; then
            abort_reason="container restarted: $container"
            return 0
        fi
    done
    return 1
}

record_container_baseline() {
    local container restarts
    : >"$log_file.restarts"
    for container in "${containers[@]}"; do
        docker inspect "$container" >/dev/null 2>&1 || continue
        restarts="$(docker inspect --format '{{.RestartCount}}' "$container")"
        printf '%s %s\n' "$container" "$restarts" >>"$log_file.restarts"
    done
}

stop_command() {
    if [[ -n "$command_pid" ]] && kill -0 "$command_pid" 2>/dev/null; then
        echo "stopping validation process group" >&2
        kill -- "-$command_pid" 2>/dev/null || true
        wait "$command_pid" 2>/dev/null || true
    fi
}

cleanup() {
    local status=$?
    if ((status != 0)); then
        stop_command
    fi
    rm -f "$log_file.restarts"
}
trap cleanup EXIT

sample_health() {
    local phase="$1"
    local timestamp temperature load psi memory swap
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    temperature="$(read_temperature_c)"
    load="$(awk '{ print $1 }' /proc/loadavg)"
    psi="$(read_cpu_psi)"
    memory="$(read_meminfo_mib MemAvailable)"
    swap="$(read_swap_used_mib)"
    printf '%s phase=%s temp_c=%s load1=%s cpu_psi_avg10=%s mem_available_mib=%s swap_used_mib=%s\n' \
        "$timestamp" "$phase" "$temperature" "$load" "$psi" "$memory" "$swap" >>"$log_file"

    if [[ "$temperature" != unavailable ]] && float_compare "$temperature >= $temperature_abort_c"; then
        abort_reason="temperature reached abort threshold"
    elif [[ "$temperature" != unavailable ]] && float_compare "$temperature >= $temperature_sustained_abort_c"; then
        ((high_temperature_samples += 1))
        ((high_temperature_samples >= 10)) && abort_reason="temperature remained above sustained threshold"
    else
        high_temperature_samples=0
    fi

    if float_compare "$load > $load_abort"; then
        ((high_load_samples += 1))
        ((high_load_samples >= 30)) && abort_reason="load remained above abort threshold"
    else
        high_load_samples=0
    fi

    if [[ "$psi" != unavailable ]] && float_compare "$psi > $psi_abort"; then
        ((high_psi_samples += 1))
        ((high_psi_samples >= 30)) && abort_reason="cpu psi remained above abort threshold"
    else
        high_psi_samples=0
    fi

    if ((memory < memory_abort_mib)); then
        ((low_memory_samples += 1))
        ((low_memory_samples >= 5)) && abort_reason="available memory remained below abort threshold"
    else
        low_memory_samples=0
    fi

    if ((swap - baseline_swap_mib > swap_growth_abort_mib)); then
        abort_reason="swap growth reached abort threshold"
    fi

    if container_failed; then
        :
    fi
    [[ -z "$abort_reason" ]]
}

preflight_sample_ok() {
    local temperature="$1" load="$2" psi="$3" memory="$4" swap="$5"
    [[ "$temperature_required" != true || "$temperature" != unavailable ]] || return 1
    [[ "$temperature" == unavailable ]] || float_compare "$temperature < $temperature_preflight_c" || return 1
    float_compare "$load < $load_preflight" || return 1
    [[ "$psi" == unavailable ]] || float_compare "$psi < $psi_preflight" || return 1
    ((memory >= memory_preflight_mib)) || return 1
    ((swap <= swap_preflight_mib)) || return 1
}

warning_present() {
    local temperature="$1" psi="$2"
    [[ "$temperature" != unavailable ]] && float_compare "$temperature >= $temperature_warning_c" && return 0
    [[ "$psi" != unavailable ]] && float_compare "$psi >= $psi_warning" && return 0
    return 1
}

last_sample_values() {
    local line
    line="$(tail -n 1 "$log_file")"
    LAST_TEMP="$(sed -n 's/.*temp_c=\([^ ]*\).*/\1/p' <<<"$line")"
    LAST_LOAD="$(sed -n 's/.*load1=\([^ ]*\).*/\1/p' <<<"$line")"
    LAST_PSI="$(sed -n 's/.*cpu_psi_avg10=\([^ ]*\).*/\1/p' <<<"$line")"
    LAST_MEMORY="$(sed -n 's/.*mem_available_mib=\([^ ]*\).*/\1/p' <<<"$line")"
    LAST_SWAP="$(sed -n 's/.*swap_used_mib=\([^ ]*\).*/\1/p' <<<"$line")"
}

run_phase() {
    local phase="$1" duration="$2" elapsed=0
    while ((elapsed < duration)); do
        if ! sample_health "$phase"; then
            echo "health guard abort: $abort_reason" >&2
            return 1
        fi
        last_sample_values
        if [[ "$phase" == preflight ]] && ! preflight_sample_ok "$LAST_TEMP" "$LAST_LOAD" "$LAST_PSI" "$LAST_MEMORY" "$LAST_SWAP"; then
            echo "health guard preflight refused acceptance command" >&2
            return 3
        fi
        if warning_present "$LAST_TEMP" "$LAST_PSI"; then
            had_warning=true
            if [[ "$phase" == preflight ]]; then
                echo "health guard warning: defer new heavy work" >&2
                return 3
            fi
        fi
        sleep "$sample_interval_s"
        ((elapsed += sample_interval_s))
    done
}

baseline_swap_mib="$(read_swap_used_mib)"
echo "health guard log: $log_file"
run_phase preflight "$preflight_duration_s"

if "$dry_run"; then
    run_phase cooldown "$cooldown_duration_s"
    echo "health guard dry run passed"
    exit 0
fi

record_container_baseline
setsid "${command[@]}" &
command_pid=$!

if ! run_phase soak "$soak_duration_s"; then
    stop_command
    exit 1
fi

if ! wait "$command_pid"; then
    echo "guarded acceptance command failed" >&2
    exit 1
fi
command_pid=""
run_phase cooldown "$cooldown_duration_s"

if "$had_warning"; then
    echo "health guard completed with warning: do not schedule additional heavy work" >&2
    exit 3
fi

echo "health guard passed"
