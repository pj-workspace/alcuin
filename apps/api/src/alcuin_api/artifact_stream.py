"""Provider-neutral, incremental separation of conversation and generated files.

The model may commit to a final answer without a synthetic completion tool. Only
an explicit artifact block creates a document; ordinary replies never do.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from xml.etree import ElementTree

from alcuin_core.contracts import EventType

ANSWER_START = "<alcuin-answer>"
ANSWER_END = "</alcuin-answer>"
ARTIFACT_START = "<alcuin-artifact"
ARTIFACT_END = "</alcuin-artifact>"
MAX_ARTIFACT_CHARS = 500_000
MAX_ARTIFACTS = 8

OUTPUT_PROTOCOL = """\
<alcuin_output_protocol>
- Tool-call preambles are progress, not the final answer. Do not emit the final answer until tools are finished.
- Begin every final answer with <alcuin-answer> and end it with </alcuin-answer>. Never call tools after opening this final-answer block. These are transport delimiters, not Markdown code fences.
- Ordinary questions need only a normal Markdown answer inside that block. Do not create an artifact for every answer.
- When the user requests a document, report, Word file, HTML page or interactive output, place the actual deliverable in a separate block INSIDE the final answer: <alcuin-artifact title="Specific title" content-type="text/markdown">complete document content</alcuin-artifact>.
- For Word use text/markdown: the platform exports real editable DOCX. For interactive pages use content-type="text/html" and write self-contained HTML with inline CSS/JavaScript. Never pretend a Markdown link is a generated binary file.
- HTML runs in an isolated preview: no external network, remote libraries, parent access, forms/navigation or credential access. Use local controls and inline SVG for interactive visuals. Do not load external assets.
- Keep the conversational explanation outside the artifact short; do not duplicate the document in chat. Several requested deliverables may use separate artifact blocks (at most 8). Each block contains one complete file without wrapping code fences.
- Do not include literal transport delimiters in examples; escape their angle brackets. Always close artifact blocks before ending the answer.
- Cite factual claims next to the supporting sentence using [[cite:s1]] with the exact citation_id returned in tool evidence. Use only evidence actually provided. Never infer citation IDs from result positions or invent sources. The same markers work inside Markdown artifacts.
</alcuin_output_protocol>"""


def final_answer_committed(text: str) -> bool:
    return text.lstrip().startswith(ANSWER_START)


def _held_suffix(text: str, markers: tuple[str, ...]) -> int:
    return max(
        (
            size
            for marker in markers
            for size in range(1, min(len(marker), len(text)) + 1)
            if text.endswith(marker[:size])
        ),
        default=0,
    )


class _MarkdownControls:
    """Find transport tokens outside Markdown literals across arbitrary chunks.

    Scanned characters are committed once. Incomplete backtick runs and possible
    control prefixes remain pending until the next chunk establishes their meaning.
    """

    def __init__(self) -> None:
        self.fence: tuple[str, int] | None = None
        self.inline_ticks = 0
        self.indent: int | None = 0
        self.escaped = False

    def scan(
        self, text: str, markers: tuple[str, ...], *, final: bool
    ) -> tuple[int, str | None]:
        index = 0
        while index < len(text):
            char = text[index]
            if self.escaped:
                self.escaped = False
            elif char == "\\" and not self.fence and not self.inline_ticks:
                self.escaped = True
            elif char in "`~":
                end = index + 1
                while end < len(text) and text[end] == char:
                    end += 1
                if end == len(text) and not final:
                    return index, None
                count = end - index
                line_start = self.indent is not None and self.indent <= 3
                if self.fence:
                    if line_start and char == self.fence[0] and count >= self.fence[1]:
                        line_end = text.find("\n", end)
                        if line_end < 0 and not final:
                            return index, None
                        suffix = text[end:line_end] if line_end >= 0 else text[end:]
                        if not suffix.strip():
                            self.fence = None
                elif char == "`" and self.inline_ticks:
                    if count == self.inline_ticks:
                        self.inline_ticks = 0
                elif line_start and count >= 3:
                    self.fence = (char, count)
                elif char == "`":
                    self.inline_ticks = count
                self.indent = None
                index = end
                continue
            elif char == "<" and not self.fence and not self.inline_ticks:
                for marker in markers:
                    if text.startswith(marker, index):
                        return index, marker
                if not final and any(
                    len(text) - index < len(marker) and marker.startswith(text[index:])
                    for marker in markers
                ):
                    return index, None
            if char in "\r\n":
                self.indent = 0
            elif char == " " and self.indent is not None:
                self.indent += 1
            elif char == "\t" and self.indent is not None:
                self.indent += 4
            else:
                self.indent = None
            index += 1
        return index, None


class ArtifactStream:
    """Consume arbitrary token boundaries; emit bounded full-artifact snapshots."""

    def __init__(self, run_id: str, *, clock: Callable[[], float] = time.monotonic):
        self.run_id = run_id
        self.clock = clock
        self.pending = ""
        self.artifact: dict | None = None
        self.count = 0
        self.protocol = False
        self.last_size = 0
        self.last_emit = 0.0
        self.total_chars = 0
        self.chat_controls = _MarkdownControls()
        self.artifact_controls: _MarkdownControls | None = None

    def feed(self, delta: str) -> list[tuple[EventType, dict]]:
        self.total_chars += len(delta)
        if self.total_chars > 2_000_000:
            raise ValueError("Generated output exceeds the safe size limit")
        self.pending += delta
        return self._drain(final=False)

    def finish(self) -> list[tuple[EventType, dict]]:
        output = self._drain(final=True)
        if self.artifact is not None:
            raise ValueError(
                "Generated artifact was interrupted before its closing delimiter"
            )
        if self.protocol:
            raise ValueError(
                "Generated answer was interrupted before its closing delimiter"
            )
        return output

    def _snapshot(self, *, final: bool = False) -> list[tuple[EventType, dict]]:
        assert self.artifact is not None
        size = len(self.artifact["content"])
        if size > MAX_ARTIFACT_CHARS:
            raise ValueError("Generated artifact exceeds 500000 characters")
        # Incomplete JSON is not a valid durable JSON resource.
        if self.artifact["content_type"] == "application/json" and not final:
            return []
        if self.artifact["content_type"] == "application/json" and final:
            try:
                json.loads(self.artifact["content"])
            except json.JSONDecodeError as exc:
                raise ValueError("Generated JSON artifact is invalid") from exc
        now = self.clock()
        if (
            not final
            and self.last_size
            and size - self.last_size < 512
            and now - self.last_emit < 0.2
        ):
            return []
        self.last_size, self.last_emit = size, now
        return [
            (
                EventType.ARTIFACT_UPDATED,
                {"artifact": dict(self.artifact), "streaming": not final},
            )
        ]

    def _drain(self, *, final: bool) -> list[tuple[EventType, dict]]:
        output: list[tuple[EventType, dict]] = []
        while self.pending:
            if self.artifact is not None:
                if self.artifact_controls:
                    consumed, marker = self.artifact_controls.scan(
                        self.pending, (ARTIFACT_END,), final=final
                    )
                    end = consumed if marker else -1
                    held = len(self.pending) - consumed
                else:
                    end = self.pending.find(ARTIFACT_END)
                    held = 0 if final else _held_suffix(self.pending, (ARTIFACT_END,))
                if end < 0:
                    visible = self.pending[:-held] if held else self.pending
                    self.pending = self.pending[-held:] if held else ""
                    self.artifact["content"] += visible
                    if visible:
                        output.extend(self._snapshot())
                    break
                self.artifact["content"] += self.pending[:end]
                self.pending = self.pending[end + len(ARTIFACT_END) :]
                output.extend(self._snapshot(final=True))
                self.artifact = None
                self.artifact_controls = None
                continue

            markers = (
                (ANSWER_START, ANSWER_END, ARTIFACT_START)
                if self.protocol
                else (ANSWER_START,)
            )
            position, marker = self.chat_controls.scan(
                self.pending, markers, final=final
            )
            if marker is None:
                visible = self.pending[:position]
                self.pending = self.pending[position:]
                if visible:
                    output.append((EventType.MESSAGE_DELTA, {"delta": visible}))
                break
            if position:
                output.append(
                    (EventType.MESSAGE_DELTA, {"delta": self.pending[:position]})
                )
                self.pending = self.pending[position:]
            if marker in (ANSWER_START, ANSWER_END):
                if marker == ANSWER_START and self.protocol:
                    raise ValueError(
                        "Generated answer contains nested transport delimiters"
                    )
                self.pending = self.pending[len(marker) :]
                self.protocol = marker == ANSWER_START
                continue
            quote = None
            header_end = -1
            for index, char in enumerate(self.pending):
                if quote:
                    if char == quote:
                        quote = None
                elif char in {"'", '"'}:
                    quote = char
                elif char == ">":
                    header_end = index
                    break
            if header_end < 0:
                if len(self.pending) > 1024:
                    raise ValueError(
                        "Generated artifact header exceeds the safe size limit"
                    )
                if final:
                    raise ValueError("Generated artifact has an incomplete header")
                break
            header = self.pending[: header_end + 1]
            if len(header) > 1024:
                raise ValueError(
                    "Generated artifact header exceeds the safe size limit"
                )
            try:
                element = ElementTree.fromstring(header[:-1] + " />")
            except ElementTree.ParseError as exc:
                raise ValueError("Generated artifact header is invalid") from exc
            title = " ".join(element.get("title", "").split())
            media_type = element.get("content-type", "text/markdown")
            if element.tag != "alcuin-artifact" or not title or len(title) > 200:
                raise ValueError("Generated artifact requires a valid title")
            if media_type not in {
                "text/markdown",
                "text/plain",
                "text/html",
                "application/json",
            }:
                raise ValueError("Generated artifact content type is unsupported")
            self.count += 1
            if self.count > MAX_ARTIFACTS:
                raise ValueError("A Run may generate at most 8 artifacts")
            self.artifact = {
                "id": f"artifact-{self.run_id}-{self.count}",
                "generation_key": str(self.count),
                "title": title,
                "kind": "html" if media_type == "text/html" else "document",
                "content_type": media_type,
                "version": 1,
                "content": "",
            }
            self.last_size = 0
            self.artifact_controls = (
                _MarkdownControls() if media_type == "text/markdown" else None
            )
            self.last_emit = self.clock()
            self.pending = self.pending[header_end + 1 :]
        return output
