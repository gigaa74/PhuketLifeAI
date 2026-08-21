"""Official GigaChat SDK transport for synchronous generation calls."""

import time

from gigachat import GigaChat


class GigaChatGenerationError(RuntimeError):
    """Raised when generation fails without exposing provider internals."""


def normalize_response_content(content):
    """Extract text without leaking reprs of SDK content objects."""
    if isinstance(content, str):
        if content.strip():
            return content
        raise GigaChatGenerationError("GigaChat returned empty content")

    if isinstance(content, (list, tuple)):
        parts = []
        for part in content:
            text = _content_part_text(part)
            if text:
                parts.append(text)
        if parts:
            return "".join(parts)
        raise GigaChatGenerationError("GigaChat returned no text content")

    text = _content_part_text(content)
    if text:
        return text
    raise GigaChatGenerationError("GigaChat returned unsupported content")


def _content_part_text(part):
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        text = part.get("text")
    else:
        text = getattr(part, "text", None)
    return text if isinstance(text, str) and text else None


def generate_text(
    messages,
    *,
    access_token,
    model,
    timeout,
    ca_bundle=None,
    temperature=0.7,
    stage="other",
    correlation_id=None,
):
    """Generate text through the official SDK and preserve the text contract."""
    client_options = {
        "access_token": access_token,
        "model": model,
        "timeout": timeout,
    }
    if ca_bundle:
        client_options["ca_bundle_file"] = ca_bundle
    else:
        client_options["verify_ssl_certs"] = True

    prompt_chars = sum(
        len(content)
        for message in messages
        if isinstance(message, dict)
        for content in (message.get("content"),)
        if isinstance(content, str)
    )
    prompt_bytes = sum(
        len(content.encode("utf-8"))
        for message in messages
        if isinstance(message, dict)
        for content in (message.get("content"),)
        if isinstance(content, str)
    )
    started_at = time.perf_counter()
    client = GigaChat(**client_options)
    try:
        response = client.chat.create({
            "model": model,
            "messages": messages,
            "temperature": temperature,
        })
        return normalize_response_content(response.messages[-1].content)
    except Exception as error:
        cause = error.__cause__ or error.__context__
        print(
            "[GENERATION_FAILURE] "
            f"correlation_id={correlation_id if correlation_id is not None else 'none'} "
            f"stage={stage} "
            f"latency_ms={round((time.perf_counter() - started_at) * 1000)} "
            f"prompt_chars={prompt_chars} "
            f"prompt_bytes={prompt_bytes} "
            f"messages_count={len(messages)} "
            f"exception_type={type(error).__name__} "
            f"cause_type={type(cause).__name__ if cause is not None else 'None'}"
        )
        raise GigaChatGenerationError("GigaChat generation failed") from error
    finally:
        client.close()
