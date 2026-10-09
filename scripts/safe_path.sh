# Sourced by the review workflows before they read files from the checkout.
#
# The checkout is the pull request's content, so a path in it can be a symlink
# to a file elsewhere on the runner. Following it would copy that file into the
# prompt, and the model could quote it back into a comment.
#
# safe_to_read PATH succeeds only for a regular file that is not itself a
# symlink and whose resolved path is inside the working directory. The resolved
# path catches a symlinked parent directory; the trailing slash in the match
# stops /work/repo-evil from passing as inside /work/repo.

safe_to_read() {
  local path=$1 workspace real
  [ -f "$path" ] || return 1
  [ -L "$path" ] && return 1
  workspace=$(pwd -P)
  real=$(realpath -- "$path") || return 1
  case "$real" in
    "$workspace"/*) return 0 ;;
  esac
  return 1
}
