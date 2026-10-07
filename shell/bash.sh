_nano_shell_ask() {
  command "${NANO_SHELL_BIN:-nano-shell}" ask "$@"
}
alias '??=_nano_shell_ask'

_nano_shell_insert() {
  local question="${READLINE_LINE#\?\? }" suggestion
  [[ -n "$question" ]] || return 0
  suggestion=$(command "${NANO_SHELL_BIN:-nano-shell}" suggest "$question") || return
  READLINE_LINE="$suggestion"
  READLINE_POINT=${#READLINE_LINE}
}

if [[ $- == *i* ]]; then
  bind -x '"\C-g":_nano_shell_insert'
fi
