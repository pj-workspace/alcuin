# @alcuin/sse-client

Browser-neutral JSON SSE transport used by Alcuin Studio and the Embed Web
Component. It tracks numeric event ids, resumes bounded transient failures with
`Last-Event-ID`, suppresses replayed sequences, understands `[DONE]`, and can
recognize protocol-specific terminal JSON events.

The callback is awaited before its event id becomes the recovery cursor. A
consumer failure therefore never acknowledges an event that it did not finish
handling.
