"""Pure command parameter validation. No filesystem, process or network access.

Only this preflight exception proves a rejected request did not start execution.
Runtime/storage/isolation errors must never be converted to this contract.
"""
from pathlib import PurePosixPath
from me3_assistant.workspace import WorkspaceError, relative

PROJECT_COMMANDS = frozenset({'python_compile', 'python_dependencies', 'python_http_check','python_project_tests'})
COMMANDS = frozenset({'python', 'python_tests'}) | PROJECT_COMMANDS
REJECTIONS = frozenset({
    'workspace_command_parameters_invalid', 'unapproved_command',
    'invalid_workspace_path', 'unsupported_workspace_extension',
    'python_file_required', 'invalid_command_arguments',
    'project_directory_command_has_no_arguments', 'HTTP_check_manifest_required',
    'invalid_command_input', 'invalid_command_timeout',
    'command_target_missing', 'HTTP_check_manifest_missing',
})
GUIDANCE = ('No command, process or execution archive was started. Check the advertised '
            'command schema and workspace_capabilities; use a relative workspace path, '
            'at most eight arguments, bounded text input, and an integer timeout of '
            '1..3 seconds for python/python_tests or 1..20 for project commands. '
            'python_tests/python_project_tests and directory inspection/compile commands accept no arguments. '
            'python_http_check requires a relative JSON check-file as its first argument. '
            'python_tests/python_project_tests accept either one existing Python test file or a test directory. '
            'List/read the current workspace to choose an existing target and check file; '
            'Correct the request or clarify; do not repeat the same rejected call. '
            'No automatic retry occurred and no execution result is implied.')


class CommandParameterError(WorkspaceError):
    def __init__(self, reason):
        if reason not in REJECTIONS:
            raise ValueError('unknown_command_parameter_rejection')
        self.reason = reason
        super().__init__(reason)


def command_rejection(reason):
    if reason not in REJECTIONS:
        raise ValueError('unknown_command_parameter_rejection')
    return dict(ok=False, status='parameter_rejected',
                error='workspace_command_parameters_rejected',
                rejection_kind='command_parameters', rejection_reason=reason,
                no_effect=True, execution_started=False, automatic_retry=False,
                guidance=GUIDANCE)


def validate_command_rejection(value):
    if not isinstance(value, dict):
        raise WorkspaceError('invalid_command_parameter_rejection')
    reason = value.get('rejection_reason')
    if not isinstance(reason, str) or reason not in REJECTIONS:
        raise WorkspaceError('invalid_command_parameter_rejection')
    expected = command_rejection(reason)
    # Equality alone accepts 1/0 as booleans; require exact field types too.
    if value != expected or any(type(value.get(k)) is not type(v) for k, v in expected.items()):
        raise WorkspaceError('invalid_command_parameter_rejection')
    return reason


def _relative(path, *, directory=False):
    try:
        relative(path, directory=directory)
    except WorkspaceError as exc:
        if str(exc) not in {'invalid_workspace_path', 'unsupported_workspace_extension'}:
            raise
        raise CommandParameterError(str(exc)) from None


def validate_command_parameters(command_id, path, arguments=None, stdin='', timeout_seconds=2):
    """Return a copied argument list or reject before any external operation."""
    if not isinstance(command_id, str) or command_id not in COMMANDS:
        raise CommandParameterError('unapproved_command')
    _relative(path, directory=command_id in {'python_tests','python_project_tests', 'python_compile', 'python_dependencies'})
    if command_id in {'python', 'python_http_check'} and not path.endswith('.py'):
        raise CommandParameterError('python_file_required')
    args = [] if arguments is None else arguments
    if (not isinstance(args, list) or len(args) > 8 or
        any(not isinstance(x, str) or len(x) > 100 or '\x00' in x for x in args) or
        command_id in {'python_tests','python_project_tests'} and args):
        raise CommandParameterError('invalid_command_arguments')
    if command_id in {'python_compile', 'python_dependencies'} and args:
        raise CommandParameterError('project_directory_command_has_no_arguments')
    if command_id == 'python_http_check':
        if not args:
            raise CommandParameterError('HTTP_check_manifest_required')
        _relative(args[0])
        if not args[0].endswith('.json'):
            raise CommandParameterError('HTTP_check_manifest_required')
    try:
        valid_input = isinstance(stdin, str) and '\x00' not in stdin and len(stdin.encode('utf-8')) <= 1000
    except UnicodeError:
        valid_input = False
    if not valid_input:
        raise CommandParameterError('invalid_command_input')
    maximum = 20 if command_id in PROJECT_COMMANDS else 3
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= maximum:
        raise CommandParameterError('invalid_command_timeout')
    return list(args)


def validate_command_targets(command_id, path, arguments, files):
    """Inspect a trusted snapshot before archive/process creation, with no I/O.

    Acquiring that snapshot can fail for storage/security reasons; those errors
    remain fatal. Call after validate_command_parameters validated shapes.
    """
    if command_id not in COMMANDS or not isinstance(files, dict):
        raise WorkspaceError('invalid_command_target_preflight_state')
    prefix = path+'/' if path else ''
    if command_id in {'python', 'python_http_check'} and path not in files:
        raise CommandParameterError('command_target_missing')
    if command_id in {'python_tests','python_project_tests'}:
        file_target = path in files and path.endswith('.py')
        directory_target = path not in files and any(
            p.startswith(prefix) and PurePosixPath(p).name.startswith('test') and p.endswith('.py')
            for p in files)
        if not (file_target or directory_target):
            raise CommandParameterError('command_target_missing')
    if command_id in {'python_compile', 'python_dependencies'} and not any(p.startswith(prefix) for p in files):
        raise CommandParameterError('command_target_missing')
    if command_id == 'python_http_check' and arguments[0] not in files:
        raise CommandParameterError('HTTP_check_manifest_missing')
