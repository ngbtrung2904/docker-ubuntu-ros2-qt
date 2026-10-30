#!/bin/bash
#
# One-time setup of the machines that the console shuts down over ssh when the
# station loses power. Run on the host, as the operator user:
#
#   scripts/install-ssh-target.sh <user@host> [user@host ...]
#
# For each target it installs this user's ssh key there (asks that machine's
# login password once), records its host key, and lets that user run shutdown
# through sudo with no password (asks the sudo password once). Then it checks,
# without shutting anything down, that both work with no terminal, which is
# how the console runs its scripts.
#
# The container copies ~/.ssh in when it starts (apply_host_ssh in
# start-qt-container.sh). Restart it, or run scripts/start-qt-container.sh
# --apply-only, after adding a target.
#
# Linux targets only. A Windows target reached with sshpass is not set up here.

if [ $# -eq 0 ]; then
    echo "Usage: $0 <user@host> [user@host ...]" >&2
    exit 1
fi

KEY="$HOME/.ssh/id_ed25519"
if [ ! -f "$KEY" ]; then
    ssh-keygen -t ed25519 -N "" -f "$KEY" || exit 1
fi

# Runs on the target, in a terminal so sudo can ask for the password.
SUDOERS_CMD='u=$(id -un)
f=/etc/sudoers.d/shutdown-$(echo "$u" | tr -c "a-zA-Z0-9_\n-" _)
echo "$u ALL=(root) NOPASSWD: /usr/sbin/shutdown, /sbin/shutdown" | sudo tee "$f.tmp" >/dev/null &&
    sudo visudo -cqf "$f.tmp" && sudo chmod 440 "$f.tmp" && sudo mv "$f.tmp" "$f"'

# Runs on the target with no terminal: lists the rule, shuts nothing down.
CHECK_CMD='sudo -n -l "$(command -v shutdown)"'

failed=()
for target in "$@"; do
    echo "== $target"
    if ! ssh-copy-id -i "$KEY.pub" -o StrictHostKeyChecking=accept-new "$target"; then
        failed+=("$target"); continue
    fi
    if ! ssh -t "$target" "$SUDOERS_CMD"; then
        echo "$target: sudoers rule not installed" >&2
        failed+=("$target"); continue
    fi
    if ssh -o BatchMode=yes "$target" "$CHECK_CMD" </dev/null >/dev/null; then
        echo "$target: ok, 'ssh $target sudo shutdown -h now' needs no password"
    else
        echo "$target: check failed" >&2
        failed+=("$target")
    fi
done

if [ ${#failed[@]} -gt 0 ]; then
    echo "Failed: ${failed[*]}" >&2
    exit 1
fi
