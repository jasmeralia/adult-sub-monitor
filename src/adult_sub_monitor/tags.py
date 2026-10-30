"""Tag display formatting: split run-together words and apply consistent casing.

ManyVids tags are often a single lowercase run such as ``deeppenetration``.
``wordsegment`` splits those using a general English word-frequency list, and a
word list corrects the cases it gets wrong. The list is layered, later layers
winning over earlier ones:

1. the default list shipped in the package (``data/tag_words.txt``),
2. the operator's file (``MANYVIDS_TAG_WORDS_FILE``, default
   ``/config/manyvids_tag_words.txt``),
3. the ``MANYVIDS_TAG_WORDS`` environment variable (comma-separated).

Entries (one per item) come in three forms:

* A single word such as ``Deepthroat`` or ``POV`` is added to the segmenter's
  vocabulary so it is never split, and its exact spelling is used for display.
* A phrase containing whitespace such as ``Solo Girl`` is used verbatim whenever
  a tag matches it with the spaces removed (``sologirl``).
* ``-Entry`` drops a previously listed entry, e.g. one of the shipped defaults.
"""

import logging
import os
import re
from collections.abc import Iterable, Mapping
from importlib import resources
from pathlib import Path

import wordsegment

logger = logging.getLogger(__name__)

TAG_WORDS_ENV = "MANYVIDS_TAG_WORDS"
TAG_WORDS_FILE_ENV = "MANYVIDS_TAG_WORDS_FILE"
DEFAULT_TAG_WORDS_FILE = "/config/manyvids_tag_words.txt"

# Probability mass given to an override word, as a fraction of the corpus. It has to
# beat the product of its parts' probabilities so the segmenter keeps it whole, yet
# stay small enough that short acronyms do not start splitting unrelated words.
_OVERRIDE_WEIGHT = 1e-5
# Only lowercase or Capitalized tokens are split; "ASMR" or "POV" are left alone.
_SPLITTABLE = re.compile(r"[A-Za-z][a-z]*")
# A split is rejected, and the word kept whole, when it looks like the segmenter
# chewing a real word or name into fragments: a piece shorter than three letters
# that is not a common function word ("satisfyer" -> "satisfy er"), a suffix
# fragment ("scissoring" -> "scissor ing"), or a piece too rare to be a real word
# ("tantaly" -> "tant aly"). Words from the word list always count as real.
_MIN_PIECE_FREQUENCY = 5e-7
_SHORT_WORDS = frozenset({"a", "at", "by", "in", "me", "my", "no", "of", "on", "or"})
_SHORT_WORDS |= frozenset({"to", "up"})
_SUFFIX_FRAGMENTS = frozenset({"ing", "ers", "ies"})

# Two segmenters share one bigram table: _segmenter carries the override words,
# _pristine keeps the stock word list for blocked-keyword matching.
_segmenter = wordsegment.Segmenter()
_pristine = wordsegment.Segmenter()
_words: dict[str, str] = {}
_phrases: dict[str, str] = {}
_saved_unigrams: dict[str, float | None] = {}


def _ensure_loaded() -> None:
    if _segmenter.unigrams:
        return
    _segmenter.load()
    _pristine.unigrams = dict(_segmenter.unigrams)
    _pristine.bigrams = _segmenter.bigrams
    _pristine.total = _segmenter.total
    _pristine.limit = _segmenter.limit
    _pristine.words = _segmenter.words


def _restore_unigrams() -> None:
    for word, original in _saved_unigrams.items():
        if original is None:
            _segmenter.unigrams.pop(word, None)
        else:
            _segmenter.unigrams[word] = original
    _saved_unigrams.clear()


def _entry_key(entry: str) -> str:
    return entry.replace(" ", "").lower()


def _resolve_layers(entries: Iterable[str]) -> list[str]:
    """Apply later entries over earlier ones, honoring ``-`` removals."""
    resolved: dict[str, str] = {}
    for raw in entries:
        entry = " ".join(raw.split())
        if not entry:
            continue
        if entry.startswith("-"):
            resolved.pop(_entry_key(entry[1:]), None)
        else:
            resolved[_entry_key(entry)] = entry
    return list(resolved.values())


def configure_tag_overrides(entries: Iterable[str]) -> None:
    """Replace the active word list; later calls discard earlier entries."""
    _ensure_loaded()
    _restore_unigrams()
    _words.clear()
    _phrases.clear()

    weight = _segmenter.total * _OVERRIDE_WEIGHT
    for entry in _resolve_layers(entries):
        key = _entry_key(entry)
        if " " in entry:
            _phrases[key] = entry
            continue
        _words[key] = entry
        if key not in _saved_unigrams:
            _saved_unigrams[key] = _segmenter.unigrams.get(key)
        _segmenter.unigrams[key] = max(_segmenter.unigrams.get(key, 0), weight)


def parse_override_lines(text: str) -> list[str]:
    entries: list[str] = []
    for line in text.splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            entries.append(entry)
    return entries


def _read_shipped_words() -> list[str]:
    resource = resources.files("adult_sub_monitor").joinpath("data/tag_words.txt")
    return parse_override_lines(resource.read_text(encoding="utf-8"))


def load_tag_overrides(env: Mapping[str, str] | None = None) -> list[str]:
    """Collect word list entries: shipped defaults, then the file, then the env var."""
    source = os.environ if env is None else env
    entries = _read_shipped_words()

    path = Path(source.get(TAG_WORDS_FILE_ENV, DEFAULT_TAG_WORDS_FILE))
    try:
        entries.extend(parse_override_lines(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not read tag word list %s: %s", path, exc)

    env_value = source.get(TAG_WORDS_ENV, "")
    entries.extend(part.strip() for part in env_value.split(",") if part.strip())
    return entries


def _is_real_word(piece: str, unigrams: Mapping[str, float]) -> bool:
    if piece in _words:
        return True
    if piece in _SUFFIX_FRAGMENTS:
        return False
    if len(piece) < 3 and piece not in _SHORT_WORDS:
        return False
    frequency: float = unigrams.get(piece, 0) / _segmenter.total
    return frequency >= _MIN_PIECE_FREQUENCY


def _split_token(token: str, segmenter: wordsegment.Segmenter) -> list[str]:
    if not _SPLITTABLE.fullmatch(token):
        return [token]
    pieces: list[str] = segmenter.segment(token)
    if len(pieces) < 2:
        return [token]
    if not all(_is_real_word(piece, segmenter.unigrams) for piece in pieces):
        return [token]
    return pieces


def split_words(text: str) -> list[str]:
    """Split ``text`` into lowercase words, breaking up run-together tokens.

    Uses the unmodified word-frequency list, ignoring the override list, so
    ``deepthroat`` yields ``deep`` and ``throat``. Blocked-keyword matching relies on
    this to treat "deepthroat" and "Deep Throat" alike.
    """
    _ensure_loaded()
    words: list[str] = []
    for token in text.split():
        words.extend(piece.lower() for piece in _split_token(token, _pristine))
    return words


def _format_word(word: str) -> str:
    return _words.get(word.lower()) or word[0].upper() + word[1:]


def format_tag(text: str) -> str:
    """Split run-together words, then capitalize each word."""
    _ensure_loaded()
    phrase = _phrases.get(_entry_key(text))
    if phrase is not None:
        return phrase

    words: list[str] = []
    for token in text.split():
        words.extend(_split_token(token, _segmenter))
    return " ".join(_format_word(word) for word in words)
