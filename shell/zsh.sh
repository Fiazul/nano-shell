_nano_shell_ask() {
  command "${NANO_SHELL_BIN:-nano-shell}" ask "$@"
}
alias '??=noglob _nano_shell_ask'

_nano_shell_insert() {
  local question="${BUFFER#\?\? }" suggestion
  [[ -n "$question" ]] || return 0
  suggestion=$(command "${NANO_SHELL_BIN:-nano-shell}" suggest "$question") || return
  BUFFER="$suggestion"
  CURSOR=${#BUFFER}
  zle redisplay
}

if [[ -o interactive ]]; then
  zle -N _nano_shell_insert
  bindkey '^G' _nano_shell_insert
fi
