class WorktreeError(Exception):
    """Refusal or failure (exit 1)."""
    exit_code = 1

class CannotRunError(WorktreeError):
    """Environment cannot run the command at all (exit 2)."""
    exit_code = 2

class RegistryError(WorktreeError):
    exit_code = 1
