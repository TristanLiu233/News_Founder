"""Tag an ingestion exception with the phase it escaped from (Operations-2 source health).

``fetch`` covers the HTTP request and the adapter's parsing/normalization; ``persist`` covers the
database transaction. The exception itself is re-raised unchanged (same type and message), so
existing callers see no difference; only an ``ingestion_phase`` attribute is added.
"""

from contextlib import contextmanager

import httpx

FETCH, PERSIST = "fetch", "persist"


@contextmanager
def ingestion_phase(name):
    try:
        yield
    except Exception as exc:
        if getattr(exc, "ingestion_phase", None) is None:
            try:
                exc.ingestion_phase = name
            except AttributeError:  # an exception type without an instance dict
                pass
        raise


def failure_phase(exc):
    """``transport`` (request/response), ``parse`` (the response could not be read into items),
    ``persist`` (the database step, including a batch whose every row was rejected), or
    ``other`` (before any request, e.g. a missing required contact)."""

    phase = getattr(exc, "ingestion_phase", None)
    if phase == FETCH:
        transport = httpx.HTTPStatusError | httpx.TransportError | httpx.TimeoutException
        return "transport" if isinstance(exc, transport | ConnectionError) else "parse"
    if phase == PERSIST:
        return "persist"
    return "other"
