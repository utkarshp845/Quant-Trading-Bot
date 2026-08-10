#!/usr/bin/env bash

set -Eeuo pipefail

profile="${1:-live}"
app_dir="${APP_DIR:-$(pwd)}"
install_cron="${INSTALL_CRON:-true}"
run_after_deploy="${RUN_AFTER_DEPLOY:-true}"
docker_bin="${DOCKER_BIN:-$(command -v docker || true)}"
market="${DEPLOY_MARKET:-spy}"
install_options_cron="${INSTALL_OPTIONS_CRON:-true}"

case "$profile" in
  live|paper) ;;
  *)
    echo "Unsupported profile: $profile" >&2
    exit 1
    ;;
esac

case "$profile" in
  live) compose_service="trade" ;;
  paper) compose_service="paper" ;;
esac

if [[ -z "$docker_bin" ]]; then
  echo "docker is required on the EC2 host" >&2
  exit 1
fi

cd "$app_dir"

if [[ ! -f .env ]]; then
  echo "Missing $app_dir/.env" >&2
  exit 1
fi

mkdir -p data logs reports runtime

echo "Building trading-bot image in $app_dir"
"$docker_bin" compose build

echo "Running ${profile} validation for ${market}"
"$docker_bin" compose run --rm --entrypoint python "$compose_service" -m bot.profile_runner "$profile" validate "$market"

echo "Checking ${profile} broker and market-data connectivity for ${market}"
"$docker_bin" compose run --rm --entrypoint python "$compose_service" -m bot.profile_runner "$profile" connectivity "$market"

if [[ "$install_options_cron" == "true" || "$install_options_cron" == "1" ]]; then
  # The options profile is always paper (not the profile/market pair being
  # deployed above) — see README.md's "One-week paper evaluation" section.
  # Validate and check connectivity for it too before scheduling it,
  # mirroring the checks above.
  echo "Running paper validation for options"
  "$docker_bin" compose run --rm --entrypoint python paper-options -m bot.profile_runner paper validate options

  echo "Checking paper broker and options market-data connectivity for options"
  "$docker_bin" compose run --rm --entrypoint python paper-options -m bot.profile_runner paper connectivity options
fi

if [[ "$install_cron" == "true" || "$install_cron" == "1" ]]; then
  echo "Installing cron schedule for ${profile}"
  bash deploy/ec2/install_cron.sh "$profile" "$app_dir"
fi

if [[ "$run_after_deploy" == "true" || "$run_after_deploy" == "1" ]]; then
  echo "Running immediate ${profile} trading cycle"
  "$docker_bin" compose run --rm "$compose_service"
fi

echo "Deployment finished for ${profile}"
