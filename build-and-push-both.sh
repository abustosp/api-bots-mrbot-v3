#!/usr/bin/env bash
# Uso: ./build-and-push-both.sh [TAG]
source "$(dirname "$0")/build-and-push-lib.sh"
bp_central
bp_worker
