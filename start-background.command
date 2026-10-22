#!/bin/zsh
set -e
cd -- "${0:A:h}"
umask 077
mkdir -p state
touch state/background.log
chmod 600 state/background.log
service_domain="gui/$(id -u)"
service_target="$service_domain/local.xiaoai-hermes"
launchctl enable "$service_target"
if launchctl print "$service_target" >/dev/null 2>&1; then
  launchctl kickstart "$service_target"
else
  launchctl bootstrap "$service_domain" "$HOME/Library/LaunchAgents/local.xiaoai-hermes.plist"
fi
print '桥接已在后台启动，并已设为登录后自动启动，可以关闭此窗口。'
