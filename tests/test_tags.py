from collections.abc import Iterator
from pathlib import Path

import pytest

from adult_sub_monitor.tags import (
    TAG_WORDS_ENV,
    TAG_WORDS_FILE_ENV,
    configure_tag_overrides,
    format_tag,
    load_tag_overrides,
    parse_override_lines,
    split_words,
)

MISSING_FILE = "/nonexistent/manyvids_tag_words.txt"


@pytest.fixture(autouse=True)
def _reset_overrides() -> Iterator[None]:
    configure_tag_overrides([])
    yield
    configure_tag_overrides([])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("deeppenetration", "Deep Penetration"),
        ("bigdildo", "Big Dildo"),
        ("naughty", "Naughty"),
        ("Nylons", "Nylons"),
        ("POV", "POV"),
        ("Panty Fetish", "Panty Fetish"),
        ("18 And 19 Yrs Old", "18 And 19 Yrs Old"),
        # Capitalized run-together tokens are split too
        ("Peefetish", "Pee Fetish"),
        ("Girlnextdoor", "Girl Next Door"),
        # all-caps tokens are left alone
        ("ASMR", "ASMR"),
        ("PAWG", "PAWG"),
    ],
)
def test_format_tag(raw: str, expected: str) -> None:
    assert format_tag(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "satisfyer",  # "satisfy er": fragment piece
        "scissoring",  # "scissor ing": suffix fragment
        "gooningencouragement",  # "go on ing ...": suffix fragment
        "Tantaly",  # "tant aly": piece too rare to be a word
    ],
)
def test_format_tag_keeps_implausible_splits_whole(raw: str) -> None:
    assert format_tag(raw) == raw.capitalize()


def test_word_override_keeps_word_whole_and_sets_display() -> None:
    assert format_tag("deepthroat") == "Deep Throat"

    configure_tag_overrides(["Deepthroat", "POV"])

    assert format_tag("deepthroat") == "Deepthroat"
    assert format_tag("bigdeepthroat") == "Big Deepthroat"
    assert format_tag("pov") == "POV"


def test_override_words_count_as_real_pieces() -> None:
    assert format_tag("dpdildo") == "Dpdildo"

    configure_tag_overrides(["DP"])

    assert format_tag("dpdildo") == "DP Dildo"


def test_phrase_override_matches_squashed_tag() -> None:
    assert format_tag("sologirl") == "Sologirl"

    configure_tag_overrides(["Solo Girl"])

    assert format_tag("sologirl") == "Solo Girl"
    assert format_tag("solo girl") == "Solo Girl"


def test_reconfigure_discards_previous_overrides() -> None:
    configure_tag_overrides(["Deepthroat"])
    configure_tag_overrides([])

    assert format_tag("deepthroat") == "Deep Throat"


def test_later_entries_win_and_dash_removes() -> None:
    configure_tag_overrides(["Deepthroat", "DEEPTHROAT"])
    assert format_tag("deepthroat") == "DEEPTHROAT"

    configure_tag_overrides(["Deepthroat", "-deepthroat"])
    assert format_tag("deepthroat") == "Deep Throat"

    configure_tag_overrides(["Solo Girl", "-Solo Girl", "Deepthroat"])
    assert format_tag("sologirl") == "Sologirl"
    assert format_tag("deepthroat") == "Deepthroat"


def test_split_words_ignores_overrides() -> None:
    configure_tag_overrides(["Deepthroat"])

    assert split_words("deepthroat") == ["deep", "throat"]
    assert split_words("Deep Penetration") == ["deep", "penetration"]
    assert split_words("anal") == ["anal"]


def test_parse_override_lines_skips_blanks_and_comments() -> None:
    text = "# header\n\nDeepthroat  # trailing\n  Solo Girl \n"

    assert parse_override_lines(text) == ["Deepthroat", "Solo Girl"]


def test_load_tag_overrides_layers_shipped_file_and_env(tmp_path: Path) -> None:
    words_file = tmp_path / "words.txt"
    words_file.write_text("# c\nSolo Girl\n-Deepthroat\n", encoding="utf-8")

    entries = load_tag_overrides(
        {TAG_WORDS_ENV: "Kinkster, POV,", TAG_WORDS_FILE_ENV: str(words_file)}
    )
    shipped = load_tag_overrides({TAG_WORDS_FILE_ENV: MISSING_FILE})

    assert "Deepthroat" in shipped
    assert entries[: len(shipped)] == shipped
    assert entries[len(shipped) :] == ["Solo Girl", "-Deepthroat", "Kinkster", "POV"]


def test_user_file_can_drop_a_shipped_default(tmp_path: Path) -> None:
    words_file = tmp_path / "words.txt"
    words_file.write_text("-Deepthroat\n", encoding="utf-8")

    configure_tag_overrides(load_tag_overrides({TAG_WORDS_FILE_ENV: str(words_file)}))

    assert format_tag("deepthroat") == "Deep Throat"
    assert format_tag("blowjob") == "Blowjob"


def test_shipped_defaults_apply() -> None:
    configure_tag_overrides(load_tag_overrides({TAG_WORDS_FILE_ENV: MISSING_FILE}))

    assert format_tag("deepthroat") == "Deepthroat"
    assert format_tag("bbcdildo") == "BBC Dildo"
    assert format_tag("stepson") == "Stepson"
    assert format_tag("skyrim") == "Skyrim"
    assert format_tag("cuminhair") == "Cum In Hair"
    assert format_tag("deeppenetration") == "Deep Penetration"


def test_shipped_defaults_have_no_duplicate_entries() -> None:
    shipped = load_tag_overrides({TAG_WORDS_FILE_ENV: MISSING_FILE})
    keys = [entry.replace(" ", "").lower() for entry in shipped]

    assert len(keys) == len(set(keys))
    assert not any(entry.startswith("-") for entry in shipped)


def test_load_tag_overrides_ignores_missing_file() -> None:
    assert load_tag_overrides({TAG_WORDS_FILE_ENV: MISSING_FILE})


def test_load_tag_overrides_tolerates_unreadable_file(tmp_path: Path) -> None:
    shipped = load_tag_overrides({TAG_WORDS_FILE_ENV: MISSING_FILE})

    assert load_tag_overrides({TAG_WORDS_FILE_ENV: str(tmp_path)}) == shipped
