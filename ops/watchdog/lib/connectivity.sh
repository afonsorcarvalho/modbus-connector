#!/bin/sh
# Motor compartilhado do connectivity-watchdog, sourced pelos scripts
# /etc/watchdog.d/check-*.
#
# O script que faz source DEVE definir antes de chamar run_watchdog_check:
#   TRACK         - nome curto, usado no arquivo de estado (ex.: "internet")
#   REBOOT_AFTER  - segundos de falha continua ate pedir reboot
#   REPAIR_EVERY  - segundos minimos entre remediacoes
#   probe()       - retorna 0 se conectado, !=0 caso contrario
#   remediate()   - dispara remediacao; retorna 0 se disparou, !=0 se pulou
#
# O daemon watchdog(8) chama cada script como:
#   <script> test            -> exit 0 saudavel, exit 1 falha
#   <script> repair <errno>  -> exit 0 tratado/keep-alive, exit 1 => reboot
#
# CONTRATO: nunca bloquear. Comandos usam timeouts curtos; um ciclo termina
# bem abaixo do test-timeout/repair-timeout do watchdog.conf.

# Garante os diretorios de ferramentas do sistema, mas deixa shims de teste
# (prependados no PATH) vencerem.
PATH="$PATH:/usr/sbin:/sbin:/usr/bin:/bin"

STATE_DIR="${WATCHDOG_STATE_DIR:-/run/watchdog}"

_now() { echo "${WATCHDOG_NOW:-$(date +%s)}"; }

_log() {
    logger -t "watchdog-conn" "$1" 2>/dev/null || true
    echo "watchdog-conn: $1" >&2
}

run_watchdog_check() {
    mode="$1"
    state_file="$STATE_DIR/$TRACK.state"

    case "$mode" in
        repair)
            mkdir -p "$STATE_DIR" 2>/dev/null || true
            now=$(_now)
            if [ -f "$state_file" ]; then
                read first_fail last_repair < "$state_file"
            else
                first_fail="$now"
                last_repair=0
            fi
            case "$first_fail" in ''|*[!0-9]*) first_fail="$now" ;; esac
            case "$last_repair" in ''|*[!0-9]*) last_repair=0 ;; esac

            elapsed=$(( now - first_fail ))
            if [ "$elapsed" -ge "$REBOOT_AFTER" ]; then
                _log "$TRACK caido ha ${elapsed}s (>= ${REBOOT_AFTER}s) - pedindo reboot por hardware"
                exit 1
            fi

            if [ $(( now - last_repair )) -ge "$REPAIR_EVERY" ]; then
                if remediate; then
                    last_repair="$now"
                    _log "$TRACK remediacao disparada (caido ${elapsed}s/${REBOOT_AFTER}s)"
                else
                    _log "$TRACK remediacao pulada (caido ${elapsed}s/${REBOOT_AFTER}s)"
                fi
            fi

            echo "$first_fail $last_repair" > "$state_file"
            exit 0
            ;;
        *)
            if probe; then
                rm -f "$state_file" 2>/dev/null || true
                exit 0
            fi
            exit 1
            ;;
    esac
}
