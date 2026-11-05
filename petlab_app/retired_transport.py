"""Owner-thread archival of a Writer whose playback authority has ended."""
from threading import get_ident


class WriterInterrupted(Exception):pass


def retain(product,work,future):
    rows=getattr(product,'_retired_transports',None)
    if rows is None:rows=[];product._retired_transports=rows
    rows.append((get_ident(),work,future))


def drain(product,*,wait=False):
    rows=getattr(product,'_retired_transports',[])
    for row in tuple(rows):
        owner,work,future=row
        if owner!=get_ident():raise RuntimeError('retired Writer archive requires owner thread')
        if not wait and not future.done():continue
        # archive() seals the original request and candidate. It never submits
        # an action, changes its source position, or adds an actual effect.
        try:
            try:future.result()
            except Exception:pass # The native finish persists the transport failure.
            work.archive()
        except Exception as error:
            import logging
            logging.getLogger(__name__).error('retired_writer_archive_failed error_type=%s',type(error).__name__)
            failures=getattr(product,'retired_transport_failures',None)
            if failures is None:failures=[];product.retired_transport_failures=failures
            failures.append(type(error).__name__)
        finally:
            rows.remove(row)
