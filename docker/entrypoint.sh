#!/bin/sh
set -e

case "${WHISPER_DEVICE:-auto}" in
    auto)
        if [ ! -e /dev/nvidiactl ]; then
            WHISPER_DEVICE=cpu
            export WHISPER_DEVICE
        fi
        ;;
esac

exec "$@"
