#!/usr/bin/env bash

set -Eeuo pipefail

profile="${1:-live}"
app_dir="${2:-${APP_DIR:-/opt/trading-bot/app}}"
cron_tz="${CRON_TZ:-America/New_York}"
schedule="${CRON_SCHEDULE:-*/5 * * * *}"
monitor_schedule="${MONITOR_CRON_SCHEDULE:-17 * * * *}"
research_schedule="${RESEARCH_CRON_SCHEDULE:-42 0 * * *}"
docker_bin="${DOCKER_BIN:-$(command -v docker || true)}"
market="${DEPLOY_MARKET:-spy}"
job_marker="# trading-bot-${profile}"

# Independent paper-options schedule: NVDA/TSLA long calls/puts
# (config/paper_options.env), the active week-long paper evaluation — see
# docs/strategy_options_2026-08.md and README.md's "One-week paper
# evaluation" section. This is installed/removed separately from the
# profile/market pair above (which stays whatever the deploy targets, e.g.
# live spy) via its own job marker, so the two schedules never interfere
# with each other's install or refresh.
install_options_cron="${INSTALL_OPTIONS_CRON:-true}"
options_schedule="${OPTIONS_CRON_SCHEDULE:-20 * * * *}"
options_monitor_schedule="${OPTIONS_MONITOR_CRON_SCHEDULE:-35 * * * *}"
options_daily_schedule="${OPTIONS_DAILY_CRON_SCHEDULE:-55 23 * * *}"
options_job_marker="# trading-bot-paper-options"

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
  echo "docker is required to install the cron job" >&2
  exit 1
fi

mkdir -p "$app_dir/logs"

trade_job="${schedule} cd ${app_dir} && ${docker_bin} compose run --rm ${compose_service} >> ${app_dir}/logs/${profile}_cron.log 2>&1 ${job_marker}"
monitor_job="${monitor_schedule} cd ${app_dir} && ${docker_bin} compose run --rm --entrypoint python ${compose_service} -m bot.profile_runner ${profile} monitor ${market} >> ${app_dir}/logs/${profile}_monitor_cron.log 2>&1 ${job_marker}"

options_trade_job="${options_schedule} cd ${app_dir} && ${docker_bin} compose run --rm paper-options >> ${app_dir}/logs/paper_options_cron.log 2>&1 ${options_job_marker}"
options_monitor_job="${options_monitor_schedule} cd ${app_dir} && ${docker_bin} compose run --rm paper-options-monitor >> ${app_dir}/logs/paper_options_monitor_cron.log 2>&1 ${options_job_marker}"
options_daily_job="${options_daily_schedule} cd ${app_dir} && ${docker_bin} compose run --rm paper-options-daily >> ${app_dir}/logs/paper_options_daily_cron.log 2>&1 ${options_job_marker}"

current_crontab="$(crontab -l 2>/dev/null || true)"
filtered_crontab="$(printf '%s\n' "$current_crontab" | grep -v '^CRON_TZ=' | grep -v -F "$job_marker" || true)"
if [[ "$install_options_cron" == "true" || "$install_options_cron" == "1" ]]; then
  # Only drop existing options-marked lines when we're about to reinstall
  # them — leaves them untouched on a deploy that opts out this time.
  filtered_crontab="$(printf '%s\n' "$filtered_crontab" | grep -v -F "$options_job_marker" || true)"
fi

{
  echo "CRON_TZ=${cron_tz}"
  printf '%s\n' "$filtered_crontab"
  echo "$trade_job"
  echo "$monitor_job"
  if [[ "$profile" == "paper" ]]; then
    echo "${research_schedule} cd ${app_dir} && ${docker_bin} compose run --rm --entrypoint python ${compose_service} -m bot.profile_runner ${profile} research ${market} >> ${app_dir}/logs/${profile}_research_cron.log 2>&1 ${job_marker}"
  fi
  if [[ "$install_options_cron" == "true" || "$install_options_cron" == "1" ]]; then
    echo "$options_trade_job"
    echo "$options_monitor_job"
    echo "$options_daily_job"
  fi
} | sed '/^[[:space:]]*$/d' | crontab -

echo "Installed ${profile} trade schedule: ${schedule} (${cron_tz})"
echo "Installed ${profile} monitor schedule: ${monitor_schedule} (${cron_tz})"
if [[ "$profile" == "paper" ]]; then
  echo "Installed ${profile} research schedule: ${research_schedule} (${cron_tz})"
fi
if [[ "$install_options_cron" == "true" || "$install_options_cron" == "1" ]]; then
  echo "Installed paper-options trade schedule: ${options_schedule} (${cron_tz})"
  echo "Installed paper-options monitor schedule: ${options_monitor_schedule} (${cron_tz})"
  echo "Installed paper-options daily-report schedule: ${options_daily_schedule} (${cron_tz})"
else
  echo "Skipped paper-options schedule (INSTALL_OPTIONS_CRON=${install_options_cron})"
fi
