"""Fixed Gmail TLS endpoints; host-authorized reads and once-only DATA attempt.

This synchronous low-level API must run outside the voice event loop. Callbacks
are trusted host objects, never model/tool arguments. It does not mint user
commands/confirmation or replace the host durable operation journal. No retry,
inbox polling, credentials in results, attachments, HTML execution or downloads.
"""
import hashlib,imaplib,json,re,smtplib,socket,ssl,threading,uuid
from dataclasses import dataclass,field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from .credentials import Credentials,address

IMAP_HOST='imap.gmail.com';SMTP_HOST='smtp.gmail.com';SMTP_PORT=587;TIMEOUT_SECONDS=12
SENT_MAX_BYTES=65536


class MailError(RuntimeError):
    def __init__(self,code,*,uncertain=False):
        super().__init__(code);self.code=code;self.uncertain=uncertain


@dataclass(frozen=True,repr=False)
class Draft:
    sender:str=field(repr=False)
    recipient:str=field(repr=False)
    subject:str=field(repr=False)
    body:str=field(repr=False)
    message_id:str=field(repr=False)
    wire:bytes=field(repr=False)
    sha256:str


def _wire(sender,recipient,subject,body,mid):
    address(sender);address(recipient)
    if not isinstance(subject,str) or not 1<=len(subject)<=200 or any(ord(c)<32 for c in subject):raise MailError('bounded_single_line_subject_required')
    if not isinstance(body,str) or not body.strip() or len(body.encode('utf-8'))>16000 or any(ord(c)<32 and c not in '\n\t' for c in body):raise MailError('bounded_plain_text_body_required')
    if not isinstance(mid,str) or re.fullmatch(r'<[0-9a-f]{32}@me3\.invalid>',mid) is None:raise MailError('owned_message_identity_required')
    message=EmailMessage(policy=policy.SMTP)
    message['From']=sender;message['To']=recipient;message['Subject']=subject;message['Message-ID']=mid
    message.set_content(body);return message.as_bytes()


def draft(credentials,recipient,subject,body):
    """Pure composition; NO mailbox/account access or send permission implied."""
    sender=address(credentials.account);recipient=address(recipient);mid='<'+uuid.uuid4().hex+'@me3.invalid>'
    wire=_wire(sender,recipient,subject,body,mid)
    return Draft(sender,recipient,subject,body,mid,wire,hashlib.sha256(wire).hexdigest())


def _close(client):
    if client is not None:
        try:client.shutdown() if hasattr(client,'shutdown') else client.close()
        except Exception:pass # A cleanup fault cannot undo a known SMTP250.


def _authorize(callback,*args):
    if not callable(callback):raise MailError('host_authorization_callback_required')
    # The host must raise on absent/obsolete command/review/confirmation. A
    # model-supplied Boolean or prose cannot satisfy this nonserializable API.
    try:accepted=callback(*args)
    except Exception:raise MailError('host_authorization_rejected') from None
    if accepted is not True:raise MailError('host_authorization_rejected')


def _transport_failure(protocol,phase,error):
    """Class/phase-only diagnostics. Never inspect or retain server prose."""
    if isinstance(error,socket.gaierror):reason='dns_failed'
    elif isinstance(error,TimeoutError):reason='socket_timeout'
    elif isinstance(error,ssl.SSLCertVerificationError):reason='tls_verification_failed'
    elif isinstance(error,ssl.SSLError):reason='tls_failed'
    elif protocol=='imap' and isinstance(error,imaplib.IMAP4.abort):reason='protocol_failed'
    elif protocol=='imap' and isinstance(error,imaplib.IMAP4.error):reason='authentication_rejected' if phase=='login' else 'protocol_failed'
    elif protocol=='smtp' and isinstance(error,smtplib.SMTPAuthenticationError):reason='authentication_rejected'
    elif protocol=='smtp' and isinstance(error,smtplib.SMTPException):reason='protocol_failed'
    elif isinstance(error,OSError):reason='connect_failed' if phase=='connect' else 'connection_lost'
    elif phase=='connect' and isinstance(error,(TypeError,ValueError)):reason='client_configuration_failed'
    else:reason='connection_or_protocol_failed'
    return MailError('gmail_'+protocol+'_'+reason)


def _immutable(value,account):
    if not isinstance(value,Draft) or value.sender!=account or hashlib.sha256(value.wire).hexdigest()!=value.sha256:raise MailError('immutable_current_account_draft_required')
    try:wire=_wire(value.sender,value.recipient,value.subject,value.body,value.message_id)
    except Exception:raise MailError('immutable_current_account_draft_required') from None
    if wire!=value.wire:raise MailError('immutable_current_account_draft_required')


def _list_string(value):
    """Decode IMAP quoted-string/atom without interpreting mailbox syntax."""
    if value.startswith(b'"'):
        out=bytearray();pos=1
        while pos<len(value):
            char=value[pos];pos+=1
            if char==34:
                if value[pos:].strip():raise MailError('sent_folder_list_unverified')
                return bytes(out)
            if char==92:
                if pos>=len(value) or value[pos] not in (34,92):raise MailError('sent_folder_list_unverified')
                char=value[pos];pos+=1
            out.append(char)
        raise MailError('sent_folder_list_unverified')
    if re.fullmatch(rb'[^(){ %*"\\\x00-\x20\x7f]+',value) is None:raise MailError('sent_folder_list_unverified')
    return value


def _sent_folder(rows):
    """SPECIAL-USE, never localized display-name guessing; handles LIST literals.

    Gmail's legacy IMAP mailbox bytes are modified UTF-7. Keeping those ASCII
    bytes opaque preserves localized names without decoding/re-encoding them.
    """
    if not isinstance(rows,list) or not 1<=len(rows)<=256:raise MailError('sent_folder_list_unverified')
    found=[];total=0;literal_tail=False
    for row in rows:
        # imaplib retains the empty line following a mailbox-name literal.
        if row==b'' and literal_tail:literal_tail=False;continue
        literal_tail=False
        literal=None
        if isinstance(row,tuple) and len(row)==2 and all(isinstance(v,bytes) for v in row):row,literal=row;literal_tail=True
        if not isinstance(row,bytes):raise MailError('sent_folder_list_unverified')
        total+=len(row)+(len(literal) if literal is not None else 0)
        if total>65536 or len(row)>8192 or any(c in row for c in (b'\r',b'\n',b'\x00')):raise MailError('sent_folder_list_unverified')
        matched=re.fullmatch(rb'\(([^()]*)\) (?:NIL|"(?:[^"\\\r\n]|\\["\\])*") (.+)',row)
        if matched is None:raise MailError('sent_folder_list_unverified')
        flags=matched[1].lower().split()
        if b'\\sent' not in flags:continue
        if b'\\noselect' in flags:raise MailError('sent_folder_unselectable')
        name=matched[2]
        if literal is not None:
            count=re.fullmatch(rb'\{([0-9]{1,5})\}',name)
            if count is None or int(count[1])!=len(literal):raise MailError('sent_folder_list_unverified')
            name=literal
        else:name=_list_string(name)
        if not 1<=len(name)<=1024 or any(c<32 or c>=127 for c in name):raise MailError('sent_folder_list_unverified')
        found.append(name)
    if len(found)!=1:raise MailError('sent_folder_ambiguous_or_missing')
    # IMAP command argument always a single escaped quoted string.
    return '"'+found[0].decode('ascii').replace('\\','\\\\').replace('"','\\"')+'"'


def _plain_identity(raw):
    message=BytesParser(policy=policy.default).parsebytes(raw)
    if message.defects or message.is_multipart() or message.get_content_type()!='text/plain' or message.get_content_disposition()=='attachment':raise MailError('sent_copy_content_unverified')
    for key in ('From','To','Subject','Message-ID'):
        values=message.get_all(key,[])
        if len(values)!=1 or getattr(values[0],'defects',()):raise MailError('sent_copy_identity_unverified')
    if any(message.get_all(key) for key in ('Cc','Bcc','Sender','Resent-From','Resent-To','Resent-Message-ID')):raise MailError('sent_copy_identity_unverified')
    try:body=(message.get_payload(decode=True) or b'').decode(message.get_content_charset() or 'ascii',errors='strict')
    except (UnicodeError,LookupError):raise MailError('sent_copy_content_unverified') from None
    # MIME transfer encodings/header folding can change in transit. Compare
    # decoded exact content after transport-only CRLF normalization, not a raw
    # hash that would reject Gmail's added authentication/trace headers.
    return dict(sender=str(message['From']),recipient=str(message['To']),subject=str(message['Subject']),message_id=str(message['Message-ID']),body=body.replace('\r\n','\n'))


class Transport:
    def __init__(self,credentials,*,imap_factory=imaplib.IMAP4_SSL,smtp_factory=smtplib.SMTP):
        if not isinstance(credentials,Credentials):raise MailError('private_credentials_object_required')
        self._credentials=credentials;self._imap_factory=imap_factory;self._smtp_factory=smtp_factory
        self._attempted=set();self._send_lock=threading.Lock()

    def _imap_auth(self):
        client=None;phase='connect'
        try:
            client=self._imap_factory(IMAP_HOST,993,ssl_context=ssl.create_default_context(),timeout=TIMEOUT_SECONDS)
            phase='login'
            status,_=client.login(self._credentials.account,self._credentials.app_password)
            if status!='OK':raise MailError('gmail_imap_authentication_rejected')
            return client
        except MailError:_close(client);raise
        except Exception as exc:_close(client);raise _transport_failure('imap',phase,exc) from None

    def _smtp_tls(self):
        """One fixed 587 route, required verified STARTTLS, never a fallback.

        The only cleartext commands are EHLO and STARTTLS. Authentication and
        message-envelope/content commands are issued only after TLS and a new
        successful EHLO. SMTP.starttls uses server_hostname for certificate
        validation through this required default SSL context.
        """
        client=None;phase='connect'
        try:
            client=self._smtp_factory(SMTP_HOST,SMTP_PORT,timeout=TIMEOUT_SECONDS)
            phase='greeting';code,_=client.ehlo()
            if code!=250:raise MailError('gmail_smtp_greeting_rejected')
            if client.has_extn('starttls') is not True:raise MailError('gmail_smtp_starttls_required')
            phase='starttls';code,_=client.starttls(context=ssl.create_default_context())
            if code!=220:raise MailError('gmail_smtp_starttls_rejected')
            phase='post_tls_greeting';code,_=client.ehlo()
            if code!=250:raise MailError('gmail_smtp_post_tls_greeting_rejected')
            return client
        except MailError:_close(client);raise
        except Exception as exc:_close(client);raise _transport_failure('smtp',phase,exc) from None

    def check_auth(self,*,authorize):
        """AUTH only. No LIST, SELECT, SEARCH, FETCH, MAIL, RCPT or DATA."""
        request=dict(operation='check_auth',account_sha256=hashlib.sha256(self._credentials.account.encode()).hexdigest())
        _authorize(authorize,request);imap=None;smtp=None;phase='connect'
        try:
            imap=self._imap_auth();_authorize(authorize,request)
            smtp=self._smtp_tls()
            phase='login'
            code,_=smtp.login(self._credentials.account,self._credentials.app_password)
            if code not in (235,503):raise MailError('gmail_smtp_authentication_rejected')
            _authorize(authorize,request)
            return dict(status='AUTHENTICATED',account_sha256=request['account_sha256'],imap_authenticated=True,smtp_authenticated=True,mailbox_selected=False,mailbox_read=False,submitted=False,automatic_retry=False)
        except MailError:raise
        except Exception as exc:raise _transport_failure('smtp',phase,exc) from None
        finally:_close(imap);_close(smtp)

    @staticmethod
    def mailbox_scope(mailbox):
        if mailbox not in ('INBOX','SENT'):raise MailError('configured_inbox_or_sent_scope_required')
        return mailbox

    def _selected_name(self,client,mailbox):
        if mailbox=='INBOX':return 'INBOX'
        status,rows=client.list('""','"*"')
        if status!='OK':raise MailError('sent_folder_list_unverified')
        return _sent_folder(rows)

    def _imap(self,mailbox='INBOX'):
        self.mailbox_scope(mailbox)
        client=None
        try:
            client=self._imap_auth()
            status,_=client.select(self._selected_name(client,mailbox),readonly=True)
            if status!='OK':raise MailError('gmail_readonly_inbox_unavailable')
            _,values=client.response('UIDVALIDITY')
            if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],bytes) or re.fullmatch(rb'[1-9][0-9]{0,19}',values[0]) is None:raise MailError('mailbox_version_unverified')
            return client,values[0].decode('ascii')
        except MailError:_close(client);raise
        except Exception:_close(client);raise MailError('gmail_imap_connection_or_auth_failed') from None

    def search_subject(self,subject,*,authorize,limit=5,mailbox='INBOX'):
        self.mailbox_scope(mailbox)
        if not isinstance(subject,str) or not 1<=len(subject)<=200 or any(ord(c)<32 for c in subject) or type(limit) is not int or not 1<=limit<=10:raise MailError('bounded_subject_search_required')
        _authorize(authorize,dict(operation='search_subject',subject=subject,limit=limit,**({'mailbox':mailbox} if mailbox!='INBOX' else {})))
        client=None
        try:
            client,version=self._imap() if mailbox=='INBOX' else self._imap(mailbox)
            _authorize(authorize,dict(operation='search_subject',subject=subject,limit=limit,**({'mailbox':mailbox} if mailbox!='INBOX' else {})))
            # A synchronizing UTF8 literal is data, never raw IMAP syntax.
            client.literal=subject.encode('utf-8')
            status,data=client.uid('SEARCH','CHARSET','UTF-8','SUBJECT')
            if status!='OK' or not isinstance(data,list) or len(data)!=1 or not isinstance(data[0],bytes) or len(data[0])>65536:raise MailError('bounded_search_result_unverified')
            values=data[0].split()
            if any(re.fullmatch(rb'[1-9][0-9]{0,19}',v) is None for v in values):raise MailError('search_uid_unverified')
            _authorize(authorize,dict(operation='search_subject',subject=subject,limit=limit,**({'mailbox':mailbox} if mailbox!='INBOX' else {})))
            return dict(mailbox=mailbox,uidvalidity=version,uids=[v.decode('ascii') for v in values[-limit:]],matches=len(values),truncated=len(values)>limit,read_only=True,content_is_untrusted=True)
        except MailError:raise
        except Exception:raise MailError('gmail_search_failed_no_retry') from None
        finally:_close(client)

    def list_recent_mail(self,*,authorize,limit=5,mailbox='INBOX'):
        """Commanded INBOX headers only, latest at most five sequence entries.

        No ALL/UID scan, bodies, attachments, flags or arbitrary mailbox input.
        Returned UID+UIDVALIDITY supports a separately commanded exact read.
        """
        self.mailbox_scope(mailbox)
        if type(limit) is not int or not 1<=limit<=5:raise MailError('bounded_recent_mail_limit_required')
        request=dict(operation='list_recent_mail',mailbox=mailbox,limit=limit);_authorize(authorize,request);client=None
        try:
            client=self._imap_auth();_authorize(authorize,request)
            status,counts=client.select(self._selected_name(client,mailbox),readonly=True)
            if status!='OK' or not isinstance(counts,list) or len(counts)!=1 or not isinstance(counts[0],bytes) or re.fullmatch(rb'[0-9]{1,12}',counts[0]) is None:raise MailError('gmail_readonly_inbox_unavailable')
            _,versions=client.response('UIDVALIDITY')
            if not isinstance(versions,list) or len(versions)!=1 or not isinstance(versions[0],bytes) or re.fullmatch(rb'[1-9][0-9]{0,19}',versions[0]) is None:raise MailError('mailbox_version_unverified')
            count=int(counts[0]);messages=[]
            if count:
                start=max(1,count-limit+1);_authorize(authorize,request)
                status,data=client.fetch(str(start)+':'+str(count),'(UID RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)]<0.8193>)')
                if status!='OK' or not isinstance(data,list) or len(data)>2*limit+1:raise MailError('recent_headers_unverified')
                rows=[v for v in data if isinstance(v,tuple)]
                if len(rows)!=count-start+1:raise MailError('recent_headers_changed_or_missing')
                sequences=set();uids=set()
                for row in rows:
                    if len(row)!=2 or not all(isinstance(v,bytes) for v in row):raise MailError('recent_headers_unverified')
                    meta,raw=row;seq=re.match(rb'([1-9][0-9]{0,11}) \(',meta);uid=re.findall(rb'\bUID ([1-9][0-9]{0,19})\b',meta);sizes=re.findall(rb'\bRFC822.SIZE ([0-9]{1,12})\b',meta)
                    if len(meta)>8192 or not 1<=len(raw)<=8192 or not raw.endswith((b'\r\n\r\n',b'\n\n')) or seq is None or not start<=int(seq[1])<=count or len(uid)!=1 or len(sizes)!=1 or int(sizes[0])<len(raw) or seq[1] in sequences or uid[0] in uids:raise MailError('recent_headers_identity_or_bound_mismatch')
                    sequences.add(seq[1]);uids.add(uid[0]);message=BytesParser(policy=policy.default).parsebytes(raw)
                    messages.append((int(seq[1]),dict(uid=uid[0].decode(),headers={k:str(message.get(k,''))[:512] for k in ('From','To','Subject','Date','Message-ID')},message_bytes=int(sizes[0]))))
                messages=[row for _,row in sorted(messages,reverse=True)]
            _authorize(authorize,request)
            return dict(mailbox=mailbox,uidvalidity=versions[0].decode(),messages=messages,returned=len(messages),read_only=True,content_is_untrusted=True,bodies_read=False,attachments_downloaded=False,automatic_retry=False)
        except MailError:raise
        except Exception:raise MailError('gmail_recent_headers_failed_no_retry') from None
        finally:_close(client)

    def read_message(self,uid,uidvalidity,*,authorize,max_bytes=32768,mailbox='INBOX'):
        self.mailbox_scope(mailbox)
        if any(not isinstance(v,str) or re.fullmatch('[1-9][0-9]{0,19}',v) is None for v in (uid,uidvalidity)) or type(max_bytes) is not int or not 1024<=max_bytes<=65536:raise MailError('exact_mailbox_uid_and_bounds_required')
        _authorize(authorize,dict(operation='read_message',mailbox=mailbox,uid=uid,uidvalidity=uidvalidity,max_bytes=max_bytes))
        client=None
        try:
            client,version=self._imap() if mailbox=='INBOX' else self._imap(mailbox)
            if version!=uidvalidity:raise MailError('mailbox_version_changed_lookup_required')
            _authorize(authorize,dict(operation='read_message',mailbox=mailbox,uid=uid,uidvalidity=uidvalidity,max_bytes=max_bytes))
            status,data=client.uid('FETCH',uid,'(UID RFC822.SIZE BODY.PEEK[]<0.'+str(max_bytes+1)+'>)')
            if status!='OK' or not isinstance(data,list):raise MailError('mail_read_unverified')
            rows=[v for v in data if isinstance(v,tuple)]
            if len(rows)!=1 or len(rows[0])!=2 or not all(isinstance(v,bytes) for v in rows[0]):raise MailError('mail_read_unverified')
            metadata,raw=rows[0];identifier=re.search(rb'\bUID ([0-9]+)\b',metadata);size=re.search(rb'\bRFC822.SIZE ([0-9]+)\b',metadata)
            if identifier is None or identifier[1].decode()!=uid or size is None or len(raw)>max_bytes+1:raise MailError('mail_read_identity_or_bound_mismatch')
            total=int(size[1]);complete=total==len(raw) and len(raw)<=max_bytes
            if total<len(raw):raise MailError('mail_read_size_mismatch')
            raw=raw[:max_bytes];message=BytesParser(policy=policy.default).parsebytes(raw)
            body='';parts=list(message.walk())
            if len(parts)>32:raise MailError('mail_mime_part_bound')
            for part in parts:
                if part.get_content_type()=='text/plain' and part.get_content_disposition()!='attachment':
                    payload=part.get_payload(decode=True) or b''
                    try:body=payload.decode(part.get_content_charset() or 'utf-8',errors='replace')
                    except LookupError:body=payload.decode('utf-8',errors='replace')
                    break
            _authorize(authorize,dict(operation='read_message',mailbox=mailbox,uid=uid,uidvalidity=uidvalidity,max_bytes=max_bytes))
            return dict(mailbox=mailbox,uid=uid,uidvalidity=version,headers={k:str(message.get(k,''))[:2000] for k in ('From','To','Subject','Date','Message-ID')},plain_text=body[:8000],plain_text_available=bool(body),message_complete=complete and len(body)<=8000,body_complete=complete and len(body)<=8000,raw_prefix_sha256=hashlib.sha256(raw).hexdigest(),raw_prefix_bytes=len(raw),message_bytes=total,read_only=True,content_is_untrusted=True,attachments_downloaded=False)
        except MailError:raise
        except Exception:raise MailError('gmail_message_read_failed_no_retry') from None
        finally:_close(client)

    def send_reviewed(self,value,*,authorize,claim_before_data):
        """Host-only: exact immutable draft review, separate confirmation and
        durable claim must be checked by callbacks. No retry/verification loop.
        A false/throwing claim sends no DATA. Mark uncertainty after lost DATA
        response; SMTP250 means submission only, never delivery/read proof.
        """
        _immutable(value,self._credentials.account)
        if not self._send_lock.acquire(blocking=False):raise MailError('smtp_transport_busy')
        try:return self._send_once(value,authorize,claim_before_data)
        finally:self._send_lock.release()

    def _send_once(self,value,authorize,claim_before_data):
        if value.sha256 in self._attempted:raise MailError('draft_already_claimed_no_retry')
        _authorize(authorize,value)
        client=None;data_started=False
        try:
            client=self._smtp_tls()
            client.login(self._credentials.account,self._credentials.app_password)
            code,_=client.mail(value.sender)
            if code!=250:raise MailError('smtp_sender_rejected')
            code,_=client.rcpt(value.recipient)
            if code not in (250,251):raise MailError('smtp_recipient_rejected')
            _authorize(authorize,value) # Current owner/generation immediately before claim.
            _authorize(claim_before_data,value) # MUST append durable host once-only claim.
            self._attempted.add(value.sha256);data_started=True
            code,_=client.data(value.wire)
            if code!=250:raise MailError('smtp_data_rejected')
            return dict(status='SMTP_ACCEPTED',smtp_code=250,draft_sha256=value.sha256,delivery_verified=False,read_verified=False,automatic_retry=False)
        except MailError:raise
        except smtplib.SMTPDataError:raise MailError('smtp_data_rejected_no_retry') from None
        except Exception:raise MailError('smtp_data_outcome_uncertain' if data_started else 'smtp_pre_data_failed_no_retry',uncertain=data_started) from None
        finally:_close(client)

    def verify_sent(self,value,*,authorize):
        """One independently authenticated exact outgoing observation, no retry.

        authorize must check the host's existing confirmed+claimed send. This
        operation exposes neither inbox results nor other Sent messages. A
        Sent copy proves presence there, never recipient delivery or reading.
        """
        _immutable(value,self._credentials.account);_authorize(authorize,value)
        client=None
        try:
            client=self._imap_auth();_authorize(authorize,value)
            status,rows=client.list('""','"*"')
            if status!='OK':raise MailError('sent_folder_list_unverified')
            folder=_sent_folder(rows);_authorize(authorize,value)
            status,_=client.select(folder,readonly=True)
            if status!='OK':raise MailError('sent_folder_readonly_unavailable')
            _,versions=client.response('UIDVALIDITY')
            if not isinstance(versions,list) or len(versions)!=1 or not isinstance(versions[0],bytes) or re.fullmatch(rb'[1-9][0-9]{0,19}',versions[0]) is None:raise MailError('mailbox_version_unverified')
            _authorize(authorize,value)
            # Owned 32-hex identifier has no spaces, quote, literal or syntax.
            status,rows=client.uid('SEARCH','HEADER','Message-ID','"'+value.message_id+'"')
            if status!='OK' or not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],bytes) or len(rows[0])>1024:raise MailError('sent_copy_search_unverified')
            matches=rows[0].split()
            if not matches:raise MailError('sent_copy_not_observed_no_retry')
            if len(matches)!=1 or re.fullmatch(rb'[1-9][0-9]{0,19}',matches[0]) is None:raise MailError('sent_copy_ambiguous_no_retry')
            uid=matches[0].decode('ascii');_authorize(authorize,value)
            status,data=client.uid('FETCH',uid,'(UID RFC822.SIZE BODY.PEEK[]<0.'+str(SENT_MAX_BYTES+1)+'>)')
            if status!='OK' or not isinstance(data,list) or not 1<=len(data)<=3:raise MailError('sent_copy_read_unverified')
            rows=[row for row in data if isinstance(row,tuple)]
            if len(rows)!=1 or len(rows[0])!=2 or not all(isinstance(v,bytes) for v in rows[0]):raise MailError('sent_copy_read_unverified')
            metadata,raw=rows[0];uids=re.findall(rb'\bUID ([0-9]+)\b',metadata);sizes=re.findall(rb'\bRFC822.SIZE ([0-9]+)\b',metadata)
            if len(metadata)>8192 or uids!=[uid.encode()] or len(sizes)!=1 or int(sizes[0])!=len(raw) or not 1<=len(raw)<=SENT_MAX_BYTES:raise MailError('sent_copy_identity_or_bound_mismatch')
            observed=_plain_identity(raw);expected=_plain_identity(value.wire)
            if observed!=expected:raise MailError('sent_copy_exact_content_mismatch')
            _authorize(authorize,value)
            content_sha=hashlib.sha256(json.dumps(observed,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            return dict(status='SENT_COPY_OBSERVED',draft_sha256=value.sha256,message_id=value.message_id,sent_copy_verified=True,submission_verified=True,delivery_verified=False,read_verified=False,mailbox_role='sent',uid=uid,uidvalidity=versions[0].decode(),raw_sha256=hashlib.sha256(raw).hexdigest(),content_sha256=content_sha,message_bytes=len(raw),verification_source='fresh_gmail_sent_exact_message_id',read_only=True,automatic_retry=False)
        except MailError:raise
        except Exception:raise MailError('gmail_sent_verification_failed_no_retry') from None
        finally:_close(client)
