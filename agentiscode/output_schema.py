"""Strukturovaný výstup agenta vynucený JSON Schematem (``--output-schema``).

Claude Code umí schema nativně (``--json-schema``) a vrátí výsledek jako
``structured_output`` ve finálním ``result`` eventu. OpenCode nativní podporu
nemá, proto se mu schema přidá na konec promptu a odpověď se parsuje z textu.

U obou runtimů se výsledek po doběhnutí ještě validuje lokálně — runtime nemusí
vynutit všechna klíčová slova schématu. Když validace neprojde, CLI agenta
navede zpět přes ``--resume`` se seznamem chyb (viz :meth:`OutputSchema.repair_prompt`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from jsonschema import exceptions as jsonschema_exceptions
from jsonschema import validators

from agentiscode.core import OPENCODE

#: Kolik chyb validace se nejvýš pošle agentovi / vypíše do logu.
MAX_REPORTED_ERRORS = 20
#: Maximální délka jedné chybové hlášky (enum s tisícem ID by zahltil prompt).
MAX_ERROR_MESSAGE_LEN = 300

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


class OutputSchemaError(ValueError):
    """Schema nejde načíst nebo samo není platné JSON Schema."""


@dataclass
class OutputSchema:
    schema: dict[str, Any]
    #: Kolikrát smí CLI agenta požádat o opravu nevalidní odpovědi.
    retries: int = 2

    def __post_init__(self) -> None:
        validator_cls = validators.validator_for(self.schema)
        try:
            validator_cls.check_schema(self.schema)
        except jsonschema_exceptions.SchemaError as exc:
            raise OutputSchemaError(f"neplatné JSON Schema: {exc.message}") from exc
        self._validator = validator_cls(self.schema)

    @classmethod
    def load(cls, path: str, *, retries: int = 2) -> "OutputSchema":
        try:
            schema = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            raise OutputSchemaError(f"nelze načíst schema {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OutputSchemaError(f"schema {path} není validní JSON: {exc}") from exc
        if not isinstance(schema, dict):
            raise OutputSchemaError(f"schema {path} musí být JSON objekt")
        return cls(schema=schema, retries=retries)

    # -- napojení na runtime -------------------------------------------------

    def runtime_args(self, adapter: str) -> list[str]:
        """Argumenty runtime CLI; Claude dostane schema nativně.

        Claude Code validuje ``--json-schema`` přes ajv s výchozím draftem a meta-schema
        jiných draftů (např. 2020-12) nezná, proto se mu ``$schema`` nepředává —
        lokální validace ho dál respektuje.
        """
        if adapter == OPENCODE:
            return []
        runtime_schema = {key: value for key, value in self.schema.items() if key != "$schema"}
        return ["--json-schema", json.dumps(runtime_schema, ensure_ascii=False, separators=(",", ":"))]

    def prompt_suffix(self, adapter: str) -> str:
        """Instrukce do promptu pro runtime bez nativní podpory schématu."""
        if adapter != OPENCODE:
            return ""
        return (
            "\n\n## Formát odpovědi\n"
            "Tvoje finální odpověď musí být POUZE jeden JSON dokument (bez Markdownu, bez ```, bez dalšího textu), "
            "který odpovídá tomuto JSON Schema:\n"
            f"{json.dumps(self.schema, ensure_ascii=False, indent=2)}\n"
        )

    # -- validace --------------------------------------------------------------

    def extract(self, result: Mapping[str, Any], final_text: str) -> tuple[Any, list[str]]:
        """Vrátí ``(hodnota, chyby)`` — hodnotu ze ``structured_output``, jinak z finálního textu."""
        value = result.get("structured_output")
        if value is None:
            parsed, error = parse_json_text(final_text)
            if error is not None:
                return None, [error]
            value = parsed
        return value, self.validate(value)

    def validate(self, value: Any) -> list[str]:
        errors = sorted(self._validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        messages = [f"{_format_path(error.absolute_path)}: {_truncate(error.message)}" for error in errors]
        if len(messages) > MAX_REPORTED_ERRORS:
            hidden = len(messages) - MAX_REPORTED_ERRORS
            messages = messages[:MAX_REPORTED_ERRORS] + [f"… a dalších {hidden} chyb"]
        return messages

    def repair_prompt(self, errors: list[str]) -> str:
        bullet_list = "\n".join(f"- {error}" for error in errors)
        return (
            "Tvoje poslední odpověď neodpovídá požadovanému JSON Schema:\n"
            f"{bullet_list}\n\n"
            "Oprav ji a odpověz znovu celou opravenou odpovědí — POUZE jedním JSON dokumentem podle schématu, "
            "bez Markdownu a bez dalšího textu."
        )


def parse_json_text(text: str) -> tuple[Any, Optional[str]]:
    """Najde JSON v textové odpovědi (toleruje ```json bloky a text kolem)."""
    raw = _FENCE_RE.sub("", (text or "").strip())
    if not raw:
        return None, "odpověď je prázdná, očekávám JSON"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        pass
    starts = [index for index in (raw.find("{"), raw.find("[")) if index >= 0]
    if starts:
        start = min(starts)
        end = raw.rfind("}" if raw[start] == "{" else "]")
        if end > start:
            try:
                return json.loads(raw[start : end + 1]), None
            except json.JSONDecodeError as exc:
                return None, f"odpověď není validní JSON: {exc}"
    return None, "odpověď neobsahuje JSON"


def _format_path(path: Any) -> str:
    out = "$"
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def _truncate(message: str) -> str:
    if len(message) <= MAX_ERROR_MESSAGE_LEN:
        return message
    return message[: MAX_ERROR_MESSAGE_LEN - 1] + "…"


__all__ = ["OutputSchema", "OutputSchemaError", "parse_json_text"]
