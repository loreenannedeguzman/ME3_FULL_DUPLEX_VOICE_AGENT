"""Causal software evidence; callback timestamps are not acoustic timestamps."""
def callback_continuity(clear,acknowledgements,captures):
    # A matching epoch can only be observed after that queue mutation. The
    # callback may run before the event loop finishes writing the clear log.
    ack=next((x for x in acknowledgements if x['epoch']==clear['epoch'] and x['capture_count']>=clear['capture_count']),None)
    capture=next((x for x in captures if x['capture_count']>clear['capture_count']),None)
    return ack,capture
