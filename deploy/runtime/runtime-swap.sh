#!/usr/bin/env bash

# Filesystem transaction used by the macOS updater. The caller must stop the
# supervised service before activating a staged runtime and before restoring it.

runtime_swap_phase="idle"
runtime_swap_app_had_previous=0
runtime_swap_venv_had_previous=0
runtime_swap_app=""
runtime_swap_next_app=""
runtime_swap_rollback_app=""
runtime_swap_venv=""
runtime_swap_next_venv=""
runtime_swap_rollback_venv=""

runtime_swap_pending() {
  [[ "${runtime_swap_phase}" != "idle" \
    && "${runtime_swap_phase}" != "restored" \
    && "${runtime_swap_phase}" != "committed" ]]
}

runtime_swap_activate() {
  runtime_swap_app="${1:?active app path is required}"
  runtime_swap_next_app="${2:?staged app path is required}"
  runtime_swap_rollback_app="${3:?app rollback path is required}"
  runtime_swap_venv="${4:?active venv path is required}"
  runtime_swap_next_venv="${5:?staged venv path is required}"
  runtime_swap_rollback_venv="${6:?venv rollback path is required}"

  [[ -d "${runtime_swap_next_app}" && -d "${runtime_swap_next_venv}" ]] || {
    echo "Both staged runtime directories must exist before activation." >&2
    return 66
  }
  [[ ! -e "${runtime_swap_rollback_app}" && ! -e "${runtime_swap_rollback_venv}" ]] || {
    echo "A previous runtime rollback directory still exists; activation aborted." >&2
    return 75
  }

  [[ ! -d "${runtime_swap_app}" ]] || runtime_swap_app_had_previous=1
  [[ ! -d "${runtime_swap_venv}" ]] || runtime_swap_venv_had_previous=1
  runtime_swap_phase="swapping"

  if [[ "${runtime_swap_app_had_previous}" == "1" ]]; then
    mv "${runtime_swap_app}" "${runtime_swap_rollback_app}" || return $?
  fi
  mv "${runtime_swap_next_app}" "${runtime_swap_app}" || return $?
  runtime_swap_phase="app-active"

  if [[ "${runtime_swap_venv_had_previous}" == "1" ]]; then
    mv "${runtime_swap_venv}" "${runtime_swap_rollback_venv}" || return $?
  fi
  mv "${runtime_swap_next_venv}" "${runtime_swap_venv}" || return $?
  runtime_swap_phase="active"
}

runtime_swap_restore_one() {
  local active="$1"
  local staged="$2"
  local rollback="$3"
  local had_previous="$4"
  local failed="${active}.failed.$$"

  if [[ "${had_previous}" == "1" ]]; then
    if [[ -d "${rollback}" ]]; then
      [[ ! -e "${failed}" ]] || {
        echo "Runtime rollback tomb already exists: ${failed}" >&2
        return 70
      }
      if [[ -d "${active}" ]] \
        && ! mv "${active}" "${failed}"; then
        return 70
      fi
      if ! mv "${rollback}" "${active}"; then
        if [[ -d "${failed}" ]]; then
          mv "${failed}" "${active}" || true
        fi
        return 70
      fi
    elif [[ ! -d "${active}" || ! -d "${staged}" ]]; then
      echo "Required runtime rollback directory is missing: ${rollback}" >&2
      return 70
    fi
  elif [[ -d "${active}" && ! -d "${staged}" ]]; then
    [[ ! -e "${failed}" ]] || {
      echo "Runtime rollback tomb already exists: ${failed}" >&2
      return 70
    }
    mv "${active}" "${failed}" || return $?
  fi

  # The caller restores app and venv before cleaning displaced replacements.
  # Cleanup here could fail between those two operations and leave a mixed
  # runtime even though both old components were still recoverable.
}

runtime_swap_reactivate_one() {
  local active="$1"
  local rollback="$2"
  local failed="${active}.failed.$$"
  local parked_old=0

  # Nothing was displaced for this component, so there is no replacement to
  # reactivate. This is the normal pre-activation interrupt case.
  [[ -d "${failed}" ]] || return 0
  [[ ! -e "${rollback}" ]] || return 70
  if [[ -d "${active}" ]]; then
    mv "${active}" "${rollback}" || return $?
    parked_old=1
  fi
  if ! mv "${failed}" "${active}"; then
    # Preserve a runnable old component when replacement reactivation fails.
    if [[ "${parked_old}" == "1" ]]; then
      mv "${rollback}" "${active}" || true
    fi
    return 70
  fi
}

runtime_swap_restore() {
  runtime_swap_pending || return 0
  runtime_swap_restore_one \
    "${runtime_swap_app}" "${runtime_swap_next_app}" \
    "${runtime_swap_rollback_app}" "${runtime_swap_app_had_previous}" || return $?
  if runtime_swap_restore_one \
    "${runtime_swap_venv}" "${runtime_swap_next_venv}" \
    "${runtime_swap_rollback_venv}" "${runtime_swap_venv_had_previous}"; then
    :
  else
    local restore_status="$?"
    # App restoration completed first. If venv restoration fails, move the
    # displaced replacement app back into service so the active pair remains
    # the same release and both old components stay available for a retry.
    runtime_swap_reactivate_one \
      "${runtime_swap_app}" "${runtime_swap_rollback_app}" || return $?
    return "${restore_status}"
  fi
  runtime_swap_phase="restored"

  # These PID-scoped tombs are inert. Remove them only after both components
  # are restored, and keep cleanup best-effort so an unlink failure cannot turn
  # a complete rollback into a mixed app/venv runtime.
  local app_failed="${runtime_swap_app}.failed.$$"
  local venv_failed="${runtime_swap_venv}.failed.$$"
  if [[ -e "${app_failed}" ]] && ! rm -rf -- "${app_failed}"; then
    echo "Warning: failed replacement app remains at ${app_failed}" >&2
  fi
  if [[ -e "${venv_failed}" ]] && ! rm -rf -- "${venv_failed}"; then
    echo "Warning: failed replacement venv remains at ${venv_failed}" >&2
  fi
}

runtime_swap_restore_trap() {
  local saved="$1"
  local signal="$2"
  if [[ -n "${saved}" ]]; then
    eval "${saved}"
  else
    trap - "${signal}"
  fi
}

runtime_swap_remove_tree() {
  rm -rf -- "$1"
}

runtime_swap_commit() {
  local app_discard="${runtime_swap_rollback_app}.discard.$$"
  local venv_discard="${runtime_swap_rollback_venv}.discard.$$"
  local app_moved=0
  local int_trap
  local term_trap
  local hup_trap
  [[ "${runtime_swap_phase}" == "active" ]] || {
    echo "A runtime swap can be committed only after activation." >&2
    return 70
  }

  [[ ! -e "${app_discard}" && ! -e "${venv_discard}" ]] || {
    echo "A runtime discard tomb already exists; commit aborted." >&2
    return 70
  }
  int_trap="$(trap -p INT)"
  term_trap="$(trap -p TERM)"
  hup_trap="$(trap -p HUP)"
  # These same-filesystem renames are the only signal-masked section. Once
  # both fixed rollback names disappear, a later interrupt cannot block the
  # next update or make the active app/venv look rollbackable.
  trap '' INT TERM HUP
  if [[ -d "${runtime_swap_rollback_app}" ]]; then
    if ! mv "${runtime_swap_rollback_app}" "${app_discard}"; then
      runtime_swap_restore_trap "${int_trap}" INT
      runtime_swap_restore_trap "${term_trap}" TERM
      runtime_swap_restore_trap "${hup_trap}" HUP
      return 70
    fi
    app_moved=1
  fi
  if [[ -d "${runtime_swap_rollback_venv}" ]] \
    && ! mv "${runtime_swap_rollback_venv}" "${venv_discard}"; then
    if [[ "${app_moved}" == "1" ]]; then
      if ! mv "${app_discard}" "${runtime_swap_rollback_app}"; then
        # Keep the still-valid old app discoverable by this transaction's
        # rollback path even when the fixed-name reverse move itself fails.
        runtime_swap_rollback_app="${app_discard}"
      fi
    fi
    runtime_swap_restore_trap "${int_trap}" INT
    runtime_swap_restore_trap "${term_trap}" TERM
    runtime_swap_restore_trap "${hup_trap}" HUP
    return 70
  fi
  runtime_swap_phase="committed"
  runtime_swap_restore_trap "${int_trap}" INT
  runtime_swap_restore_trap "${term_trap}" TERM
  runtime_swap_restore_trap "${hup_trap}" HUP

  # Cleanup is deliberately after commit and uses unique names. A signal or
  # filesystem error may leave an inert tomb, but never a fixed `.rollback`
  # directory and never an ambiguous active runtime.
  if [[ -e "${app_discard}" ]] \
    && ! runtime_swap_remove_tree "${app_discard}"; then
    echo "Warning: old app discard remains at ${app_discard}" >&2
  fi
  if [[ -e "${venv_discard}" ]] \
    && ! runtime_swap_remove_tree "${venv_discard}"; then
    echo "Warning: old venv discard remains at ${venv_discard}" >&2
  fi
}
