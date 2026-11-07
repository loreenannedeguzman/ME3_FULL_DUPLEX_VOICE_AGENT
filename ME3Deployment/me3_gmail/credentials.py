"""Read one private Pi credential file; never write, prompt, log or authenticate."""
import json,os,re,stat
from dataclasses import dataclass,field
from pathlib import Path


class CredentialError(ValueError):
    """Fixed-code failure; exception text never includes supplied credentials."""


@dataclass(frozen=True,repr=False)
class Credentials:
    account:str=field(repr=False)
    app_password:str=field(repr=False)


def address(value):
    # Deliberately one ASCII addr-spec, no display name/header list/SMTPUTF8.
    if (not isinstance(value,str) or len(value)>254 or
        re.fullmatch(r"[A-Za-z0-9_+%-]+(?:\.[A-Za-z0-9_+%-]+)*@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",value) is None):
        raise CredentialError('single_ascii_mail_address_required')
    return value


def parse(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise CredentialError('duplicate_credential_field')
            result[key]=value
        return result
    try:
        if not isinstance(raw,bytes) or len(raw)>4096:raise CredentialError('bounded_credential_file_required')
        value=json.loads(raw,object_pairs_hook=pairs)
        if not isinstance(value,dict) or set(value)!={'version','provider','account','app_password'} or type(value['version']) is not int or value['version']!=1 or value['provider']!='gmail_app_password':
            raise CredentialError('exact_gmail_credential_contract_required')
        account=address(value['account']);password=value['app_password']
        if not isinstance(password,str) or re.fullmatch('[A-Za-z0-9]{16}',password) is None:raise CredentialError('dedicated_app_password_required')
        return Credentials(account,password)
    except CredentialError:raise
    except (ValueError,UnicodeError,TypeError,RecursionError):raise CredentialError('invalid_private_credential_file') from None


def private_file(info,uid):
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600
        or info.st_uid!=uid or info.st_nlink!=1 or not 1<=info.st_size<=4096):
        raise CredentialError('private_owned_mode600_single_file_required')


def load(root):
    """Fixed root/data/gmail-credentials.json, POSIX ownership/no-follow path.

    The file must be entered by the owner through a future private local setup
    flow, never through conversation or model arguments. This creates nothing.
    """
    if os.name!='posix':raise CredentialError('pi_posix_private_file_required')
    root=Path(root).absolute()
    if root!=root.resolve():raise CredentialError('direct_project_root_required')
    directory=None;handle=None
    try:
        directory=os.open(root.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        for name in [*root.parts[1:],'data']:
            next_fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=next_fd
        if os.fstat(directory).st_uid!=os.getuid():raise CredentialError('owned_private_credential_directory_required')
        handle=os.open('gmail-credentials.json',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=directory)
        before=os.fstat(handle);private_file(before,os.getuid())
        raw=os.read(handle,4097);after=os.fstat(handle)
        current=os.stat('gmail-credentials.json',dir_fd=directory,follow_symlinks=False)
        signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_mode,s.st_nlink,s.st_uid)
        if signature(before)!=signature(after) or signature(after)!=signature(current):raise CredentialError('credential_file_changed_during_read')
        return parse(raw)
    except CredentialError:raise
    except OSError:raise CredentialError('private_credential_file_unavailable') from None
    finally:
        if handle is not None:os.close(handle)
        if directory is not None:os.close(directory)
