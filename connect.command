#!/bin/zsh
cd -- "${0:A:h}"
./.venv/bin/python bridge.py "$@"
result=$?
if (( result != 0 )); then
  print '连接尚未完成，请检查不含账号密码的错误提示和 README。'
fi
read -r '?按回车关闭窗口。'
exit "$result"
