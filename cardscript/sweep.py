"""Coverage sweep: parse every script under some folders and count what parsed.

`sweep` walks each folder, skips excluded sub-folders, and parses every
`.txt` file with `parse_card`. A file that raises is a failure, never a crash
and never a skip: a `ParseError` is grouped under its `cause`, and any other
exception (a parser bug, or a file that isn't UTF-8) under
`unexpected <ExceptionType>`. docs/sweep.md has the report format.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from cardscript.model import Card
from cardscript.parser import ParseError, parse_card

DEFAULT_EXCLUDE = ("rebalanced", "upcoming")


@dataclass
class Failure:
    file: str
    cause: str
    detail: str  # the offending line for a ParseError, the exception message otherwise


@dataclass
class SweepReport:
    seen: int = 0
    parsed: int = 0
    failures: list[Failure] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Percentage of scripts that parsed, 0.0 when there were none."""
        return 100.0 * self.parsed / self.seen if self.seen else 0.0

    @property
    def coverage_text(self) -> str:
        """Coverage to two decimals, rounded down: `100.00%` only if every script parsed."""
        hundredths = self.parsed * 10000 // self.seen if self.seen else 0
        return f"{hundredths // 100}.{hundredths % 100:02d}%"

    def causes(self) -> list[tuple[str, int, str]]:
        """(cause, count, first file) per cause, most common first, ties by cause."""
        counts = Counter(f.cause for f in self.failures)
        first: dict[str, str] = {}
        for f in self.failures:
            first.setdefault(f.cause, f.file)
        return sorted(((c, n, first[c]) for c, n in counts.items()), key=lambda t: (-t[1], t[0]))

    def format(self, top: int = 10) -> str:
        lines = [
            f"scripts   {self.seen}",
            f"parsed    {self.parsed}",
            f"coverage  {self.coverage_text}",
        ]
        causes = self.causes()
        if causes:
            lines.append("failures by cause:")
            width = len(str(causes[0][1]))
            lines += [f"  {n:>{width}}  {cause}  ({file})" for cause, n, file in causes[:top]]
            if len(causes) > top:
                lines.append(f"  ... {len(causes) - top} more, raise --top to list them")
        return "\n".join(lines) + "\n"


def script_files(root: Path, exclude: Iterable[str] = DEFAULT_EXCLUDE) -> Iterator[tuple[str, Path]]:
    """(label, path) for each `.txt` under `root`, in sorted path order.

    A sub-folder whose name is in `exclude` is skipped at any depth. The label
    is the path from `root`'s parent, with `/`: `cardsfolder/a/abc.txt`.
    """
    excluded = set(exclude)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded)
        for name in sorted(filenames):
            if name.endswith(".txt"):
                path = Path(dirpath, name)
                yield path.relative_to(root.parent).as_posix(), path


def sweep(
    roots: Iterable[str | Path],
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
    on_card: Callable[[str, Card], None] | None = None,
) -> SweepReport:
    """Parse every script under `roots` and count the results.

    `on_card(label, card)` is called for each script that parses, in walk order.
    """
    exclude = tuple(exclude)
    report = SweepReport()
    for root in roots:
        for label, path in script_files(Path(root), exclude):
            report.seen += 1
            try:
                card = parse_card(path.read_text(encoding="utf-8-sig"))
            except ParseError as e:
                report.failures.append(Failure(label, e.cause, e.line))
            except Exception as e:  # noqa: BLE001 - every file must be counted, see module doc
                report.failures.append(Failure(label, f"unexpected {type(e).__name__}", str(e)))
            else:
                report.parsed += 1
                if on_card is not None:
                    on_card(label, card)
    return report
