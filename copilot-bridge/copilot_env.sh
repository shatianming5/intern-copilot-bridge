#!/usr/bin/env bash
# Environment for Copilot-CLI interns (cross-platform: Linux + macOS).
# Sourced by the keeper's tmux supervisor loops and by the stream pollers.
for d in "$HOME"/.local/node-*/bin; do
  [ -d "$d" ] && export PATH="$d:$PATH"
done
# Token only needed on headless boxes; macOS copilot uses the login keychain.
if [ -f "$HOME/.copilot_token" ]; then
  export COPILOT_GITHUB_TOKEN="$(cat "$HOME/.copilot_token")"
fi
export COPILOT_ALLOW_ALL=1
