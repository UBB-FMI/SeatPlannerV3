from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import Request


CATALOG_PATH = Path(__file__).parent / "locales" / "messages.csv"
LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
PLACEHOLDER = re.compile(r"\{(\d+)\}")


@dataclass(frozen=True)
class Catalog:
    rows: dict[str, dict[str, str]]
    languages: tuple[str, ...]
    patterns: tuple[tuple[re.Pattern[str], str], ...]

    def match_language(self, requested: str | None) -> str | None:
        value = (requested or "").strip().lower()
        for code in self.languages:
            if code.lower() == value:
                return code
        base = value.split("-", 1)[0]
        return next((code for code in self.languages if code.lower() == base), None)

    def language(self, requested: str | None) -> str:
        return self.match_language(requested) or "en"

    def translate(self, source: str, language: str, *values: object) -> str:
        code = self.language(language)
        row = self.rows.get(source)
        captures = tuple(str(value) for value in values)
        if row is None and not captures:
            for pattern, key in self.patterns:
                match = pattern.fullmatch(source)
                if match:
                    row = self.rows[key]
                    captures = match.groups()
                    break
        template = (row.get(code) or row["en"]) if row else source
        return PLACEHOLDER.sub(lambda match: captures[int(match.group(1))] if int(match.group(1)) < len(captures) else match.group(), template)

    def public(self) -> dict:
        return {"languages": self.languages, "messages": {source: {code: row[code] for code in self.languages if code != "en"} for source, row in self.rows.items()}}


@lru_cache(maxsize=4)
def _read_catalog(path: str, mtime_ns: int, size: int) -> Catalog:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if columns[:3] != ["source", "context", "en"]:
            raise ValueError("Translation CSV must start with source,context,en.")
        language_columns = columns[2:]
        if any(not LANGUAGE_CODE.fullmatch(code) for code in language_columns):
            raise ValueError("Translation CSV language columns must use language codes such as ro or pt-BR.")
        rows: dict[str, dict[str, str]] = {}
        for number, row in enumerate(reader, 2):
            source = (row.get("source") or "").strip()
            if not source or source in rows or row.get("en") != source or None in row:
                raise ValueError(f"Invalid or duplicate translation source on CSV row {number}.")
            for code in language_columns:
                value = row.get(code) or ""
                if value and set(PLACEHOLDER.findall(value)) != set(PLACEHOLDER.findall(source)):
                    raise ValueError(f"Translation placeholders differ on CSV row {number}, language {code}.")
            rows[source] = row
    languages = ("en",) + tuple(code for code in language_columns if code != "en" and all(row.get(code) for row in rows.values()))
    patterns = []
    for source in rows:
        if PLACEHOLDER.search(source):
            pieces = PLACEHOLDER.split(source)
            expression = "".join(re.escape(piece) if index % 2 == 0 else "(.+?)" for index, piece in enumerate(pieces))
            patterns.append((re.compile(expression, re.DOTALL), source))
    patterns.sort(key=lambda pair: len(pair[1]), reverse=True)
    return Catalog(rows, languages, tuple(patterns))


def catalog() -> Catalog:
    stat = CATALOG_PATH.stat()
    return _read_catalog(str(CATALOG_PATH), stat.st_mtime_ns, stat.st_size)


def request_language(request: Request) -> str:
    messages = catalog()
    chosen = request.cookies.get("seatplan_lang")
    if chosen:
        return messages.language(chosen)
    accepted = []
    for index, entry in enumerate(request.headers.get("accept-language", "").split(",")):
        code, _, parameters = entry.strip().partition(";")
        try:
            quality = float(parameters.strip().removeprefix("q=")) if parameters else 1.0
        except ValueError:
            continue
        if quality > 0:
            accepted.append((-quality, index, code))
    for _, _, requested in sorted(accepted):
        code = messages.match_language(requested)
        if code:
            return code
    return "en"
