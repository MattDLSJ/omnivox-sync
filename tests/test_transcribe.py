"""Transcription correctness.

These cover the two failures that made transcripts lose content silently:
`-nt` skipping audio in the long-form loop, and one-shot language detection
deleting every passage spoken in another language. The parsing and grouping
helpers are pure, so they are tested without whisper or ffmpeg present.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.transcribe import (
    LanguageRun,
    build_runs,
    find_gaps,
    find_unreliable,
    parse_detections,
    parse_segments,
    render,
    repetition,
    snap,
    timeline,
    transcript_header,
)

# --------------------------------------------------------------------------
# Parsing whisper output
# --------------------------------------------------------------------------


DETECT_STDERR = """
whisper_model_load: n_langs       = 100
main: processing 'probe/w00000.wav' (240640 samples, 15.0 sec), 4 threads ...
whisper_full_with_state: auto-detected language: fr (p = 0.998777)
main: processing 'probe/w00001.wav' (239616 samples, 15.0 sec), 4 threads ...
whisper_full_with_state: auto-detected language: en (p = 0.999814)
"""


def test_parse_detections_pairs_each_file_with_its_language():
    assert parse_detections(DETECT_STDERR) == [
        ("probe/w00000.wav", "fr", pytest.approx(0.998777)),
        ("probe/w00001.wav", "en", pytest.approx(0.999814)),
    ]


def test_parse_detections_ignores_noise_and_missing_pairs():
    assert parse_detections("nothing here\nload_backend: loaded BLAS backend") == []


def test_parse_segments_reads_timestamps_and_applies_the_run_offset():
    stdout = (
        "[00:00:00.000 --> 00:00:03.060]   Bon, alors on reprend.\n"
        "[00:00:03.360 --> 00:00:07.540]   On parlait de la revolution.\n"
    )
    assert parse_segments(stdout, offset=120.0) == [
        (120.0, "Bon, alors on reprend."),
        (pytest.approx(123.36), "On parlait de la revolution."),
    ]


def test_parse_segments_keeps_untimestamped_lines_rather_than_dropping_them():
    """The parser must never be the thing that loses content."""
    stdout = "[00:00:01.000 --> 00:00:02.000] Premiere.\nune ligne sans horodatage\n"
    assert [text for _, text in parse_segments(stdout)] == [
        "Premiere.",
        "une ligne sans horodatage",
    ]


# --------------------------------------------------------------------------
# Grouping windows into language runs
# --------------------------------------------------------------------------


def _windows(*spec, size=15.0):
    """(lang, prob) pairs -> consecutive probe windows."""
    return [(i * size, (i + 1) * size, lang, prob) for i, (lang, prob) in enumerate(spec)]


def test_a_single_language_collapses_to_one_run():
    runs = build_runs(_windows(("fr", 0.99), ("fr", 0.98), ("fr", 0.99)))
    assert runs == [LanguageRun(0.0, 45.0, "fr", pytest.approx(0.99))]


def test_a_real_switch_becomes_separate_runs():
    """The case that used to delete the English entirely."""
    runs = build_runs(
        _windows(*([("fr", 0.99)] * 3 + [("en", 0.99)] * 3 + [("fr", 0.99)] * 3))
    )
    assert [(r.lang, r.start, r.end) for r in runs] == [
        ("fr", 0.0, 45.0),
        ("en", 45.0, 90.0),
        ("fr", 90.0, 135.0),
    ]


def test_an_unconfident_window_inherits_instead_of_splitting_the_run():
    """A boundary window measured p=0.56 in the real clip; it must not cut."""
    runs = build_runs(_windows(("fr", 0.99), ("fr", 0.56), ("fr", 0.99)))
    assert len(runs) == 1 and runs[0].lang == "fr"


def test_a_language_outside_the_allowlist_is_treated_as_noise():
    runs = build_runs(
        _windows(("fr", 0.99), ("nl", 0.97), ("fr", 0.99)), allowed=("fr", "en")
    )
    assert len(runs) == 1 and runs[0].lang == "fr"


def test_a_run_too_short_to_be_a_real_switch_is_absorbed():
    """One 15 s window of "en" inside French is noise, not a citation."""
    runs = build_runs(
        _windows(("fr", 0.99), ("fr", 0.99), ("en", 0.99), ("fr", 0.99), ("fr", 0.99)),
        min_run=20.0,
    )
    assert len(runs) == 1 and runs[0].lang == "fr"
    assert runs[0].start == 0.0 and runs[0].end == 75.0


def test_leading_unconfident_windows_inherit_forwards():
    runs = build_runs(_windows(("", 0.0), ("fr", 0.99), ("fr", 0.99)))
    assert len(runs) == 1 and runs[0].lang == "fr" and runs[0].start == 0.0


def test_no_confident_window_still_returns_one_usable_run():
    runs = build_runs(_windows(("fr", 0.20), ("de", 0.15)))
    assert len(runs) == 1 and runs[0].start == 0.0


def test_no_windows_means_no_runs():
    assert build_runs([]) == []


# --------------------------------------------------------------------------
# Boundary snapping
# --------------------------------------------------------------------------


def test_a_cut_moves_into_a_nearby_silence():
    assert snap(45.0, [12.0, 43.5, 80.0], window=6.0) == 43.5


def test_a_cut_stays_put_when_every_silence_is_too_far():
    assert snap(45.0, [12.0, 80.0], window=6.0) == 45.0


def test_a_cut_stays_put_when_silence_detection_failed():
    assert snap(45.0, [], window=6.0) == 45.0


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_a_monolingual_transcript_carries_no_language_marker():
    body = render([(LanguageRun(0.0, 30.0, "fr"), [(0.0, "Bonjour."), (5.0, "On commence.")])])
    assert body == "Bonjour.\nOn commence."
    assert "Passage" not in body


def test_a_switch_is_marked_inline_so_it_survives_rag_chunking():
    body = render([
        (LanguageRun(0.0, 30.0, "fr"), [(0.0, "Bonjour.")]),
        (LanguageRun(30.0, 60.0, "en"), [(30.0, "Now in English.")]),
    ])
    assert "**[Passage en français]**" in body
    assert "**[Passage en anglais]**" in body
    assert body.index("français") < body.index("anglais")


def test_timestamps_are_rendered_from_absolute_offsets_when_asked():
    body = render(
        [(LanguageRun(0.0, 30.0, "fr"), [(3725.0, "Tard dans le cours.")])],
        timestamps=True, annotate=False,
    )
    assert body == "[01:02:05] Tard dans le cours."


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------


def test_header_carries_the_context_that_was_previously_thrown_away():
    header = transcript_header(
        "Histoire", "8", datetime(2026, 10, 23, 8, 10), "Cours 8.m4a",
        room="Z016", block="T", teacher="Camille Nadeau", minutes=225,
    )
    assert "Z016" in header
    assert "théorie" in header
    assert "Camille Nadeau" in header
    assert "225 min" in header


def test_header_omits_fields_it_was_not_given():
    header = transcript_header("Histoire", "8", datetime(2026, 10, 23), "x.m4a")
    assert "Local" not in header and "Enseignant" not in header


def test_header_uses_no_em_dash():
    """Standing style rule: em dashes never reach generated files."""
    header = transcript_header("Histoire", "8", datetime(2026, 10, 23), "x.m4a")
    assert "—" not in header


# --------------------------------------------------------------------------
# Reading the shape of the class: gaps and unreliable stretches
# --------------------------------------------------------------------------


def test_a_long_stretch_with_no_speech_is_reported():
    """Thirty quiet minutes is information, not an absence of information."""
    segments = [(10.0, "Avant."), (1900.0, "Après.")]
    assert find_gaps(segments, 2000.0) == [(10.0, 1900.0)]


def test_normal_pauses_between_sentences_are_not_gaps():
    segments = [(0.0, "Une."), (4.0, "Deux."), (9.0, "Trois.")]
    assert find_gaps(segments, 12.0) == []


def test_a_gap_before_the_first_word_counts():
    """Recording starts, the room is still filling up."""
    assert find_gaps([(300.0, "Bon, on commence.")], 400.0) == [(0.0, 300.0)]


def test_a_gap_after_the_last_word_counts():
    assert find_gaps([(10.0, "Fini.")], 600.0) == [(10.0, 600.0)]


def _varied(sentences: int, seed: int = 7) -> str:
    """Text with the bigram diversity of real speech.

    A repeated sentence template is NOT a valid stand-in for healthy speech:
    it scores 0.20 unique bigrams, worse than an actual whisper loop.
    """
    import random

    pool = (
        "révolution industrielle bourgeoisie urbaine campagne marché ouvrier syndicat "
        "logement épidémie alphabétisation chemin filature travail police politique "
        "ville population densité salaire grève patron usine charbon vapeur"
    ).split()
    rng = random.Random(seed)
    return " ".join(" ".join(rng.sample(pool, 9)) + "." for _ in range(sentences))


def test_repetition_separates_real_speech_from_a_whisper_loop():
    """Thresholds come from measured real recordings, not from intuition."""
    healthy = _varied(40)
    looped = " ".join(["je vais vous parler de ça"] * 60)
    good_bigram, _ = repetition(healthy)
    bad_bigram, bad_gzip = repetition(looped)
    # The bigram ratio is what separates them reliably; real lectures measured
    # 0.71 to 0.81 and a real whisper loop measured 0.23.
    assert bad_bigram < 0.45 < good_bigram
    assert bad_gzip > 5.0


def test_a_looping_window_is_flagged_as_unreliable():
    segments = [(float(i), "je vais vous parler de ça") for i in range(60)]
    flagged = find_unreliable(segments)
    assert flagged and flagged[0][0] == 0.0


def test_a_healthy_window_is_not_flagged():
    sentences = _varied(60).split(". ")
    segments = [(float(i * 2), text) for i, text in enumerate(sentences)]
    assert find_unreliable(segments) == []


def test_too_little_text_is_not_judged():
    """A short answer is not evidence of a hallucination loop."""
    assert find_unreliable([(0.0, "oui"), (1.0, "oui"), (2.0, "oui")]) == []


def test_the_unreliable_marker_tells_a_reader_not_to_quote_it():
    segments = [(float(i), "je vais vous parler de ça") for i in range(60)]
    body = render([(LanguageRun(0.0, 60.0, "fr"), segments)], total=60.0)
    assert "peu fiable" in body
    assert "ne doit pas être cité" in body
    assert "Fin du passage peu fiable" in body


def test_a_gap_is_marked_inline_where_it_happens():
    body = render(
        [(LanguageRun(0.0, 2000.0, "fr"), [(10.0, "Avant."), (1900.0, "Après.")])],
        total=2000.0,
    )
    assert "Aucune parole transcrite pendant 32 min" in body
    assert body.index("Avant.") < body.index("Aucune parole") < body.index("Après.")


def test_the_timeline_summarises_what_happened():
    segments = [(10.0, "Avant."), (1900.0, "Après.")]
    block = timeline(segments, 2000.0, find_gaps(segments, 2000.0), [])
    assert "Déroulement" in block
    assert "33 min" in block            # total recording
    assert "aucune parole transcrite" in block


def test_a_clean_lecture_claims_nothing_it_cannot_back():
    """No detection is not the same as detecting nothing.

    An "aucun passage douteux" line would assert the detector found the file
    clean, when all it did was fail to flag anything.
    """
    segments = [(float(i * 5), f"Phrase {i}.") for i in range(100)]
    block = timeline(segments, 500.0, [], [])
    assert "Déroulement" in block
    assert "aucun" not in block.lower()
    assert "8 min" in block


def test_annotation_can_be_switched_off_entirely():
    body = render(
        [(LanguageRun(0.0, 2000.0, "fr"), [(10.0, "Avant."), (1900.0, "Après.")])],
        total=2000.0, annotate=False,
    )
    assert body == "Avant.\nAprès."


def test_a_small_vocabulary_alone_is_not_treated_as_a_loop():
    """A lecture drilling a short word list compresses well but is real speech.

    Compression ratio only counts when the bigram ratio agrees, so this must
    not be flagged even though it gzips like a hallucination.
    """
    text = _varied(40)
    bigram, gzip_ratio = repetition(text)
    assert bigram > 0.60 and gzip_ratio > 4.0    # the trap
    segments = [(float(i * 2), t) for i, t in enumerate(text.split(". "))]
    assert find_unreliable(segments) == []


# --------------------------------------------------------------------------
# Short foreign passages must never disappear in silence
# --------------------------------------------------------------------------


def test_a_short_foreign_passage_is_recorded_when_it_is_absorbed():
    """A 15 s English quotation cannot survive as its own run, but losing it
    without saying so is the worst possible outcome for a study transcript."""
    runs = build_runs(_windows(("fr", 0.99), ("fr", 0.99), ("en", 0.99),
                               ("fr", 0.99), ("fr", 0.99)), min_run=20.0)
    assert len(runs) == 1 and runs[0].lang == "fr"
    assert runs[0].absorbed == ((30.0, 45.0, "en"),)


def test_absorbing_a_same_language_run_records_nothing():
    runs = build_runs(_windows(("fr", 0.99), ("fr", 0.20), ("fr", 0.99)), min_run=20.0)
    assert runs[0].absorbed == ()


def test_the_lost_passage_is_announced_in_the_transcript():
    body = render(
        [(LanguageRun(0.0, 90.0, "fr", 0.99, ((30.0, 45.0, "en"),)),
          [(0.0, "Avant."), (50.0, "Après.")])],
        total=90.0, skipped=[(30.0, 45.0, "en")],
    )
    assert "n'ont pas été transcrites" in body
    # The phrase appears twice: once in the Déroulement summary, once inline.
    # It is the inline one that has to sit between the two spoken lines.
    inline = body.rindex("15 s en anglais")
    assert body.index("Avant.") < inline < body.index("Après.")


def test_the_lost_passage_also_appears_in_the_timeline():
    block = timeline([(0.0, "x")], 90.0, [], [], [(30.0, 45.0, "en")])
    assert "15 s en anglais" in block
    assert "00:00:30" in block


# --------------------------------------------------------------------------
# Voice activity detection
# --------------------------------------------------------------------------


def test_vad_is_found_in_the_cache_when_present(tmp_path, monkeypatch):
    import src.transcribe as mod

    model = tmp_path / "ggml-silero-v5.1.2.bin"
    model.write_bytes(b"x")
    monkeypatch.setattr(mod, "DEFAULT_VAD", model)
    assert mod.find_vad() == model


def test_a_missing_vad_model_is_not_fatal(tmp_path, monkeypatch):
    """A class recording must never fail because an optional model is absent."""
    import src.transcribe as mod

    monkeypatch.setattr(mod, "DEFAULT_VAD", tmp_path / "nope" / "absent.bin")
    assert mod.find_vad() is None


def test_an_explicit_vad_path_wins(tmp_path, monkeypatch):
    import src.transcribe as mod

    chosen = tmp_path / "custom.bin"
    chosen.write_bytes(b"x")
    assert mod.find_vad(str(chosen)) == chosen


def test_the_vad_flags_reach_the_whisper_command(tmp_path, monkeypatch):
    """The whole point is `--vad -vm <model>`; assert it is actually passed."""
    import subprocess as sp

    import src.transcribe as mod

    seen = {}

    def fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        return sp.CompletedProcess(cmd, 0, "[00:00:00.000 --> 00:00:01.000] Bonjour.\n", "")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    vad = tmp_path / "silero.bin"
    vad.write_bytes(b"x")
    mod._decode(
        tmp_path / "a.wav", binary="whisper-cli", model=tmp_path / "m.bin",
        language="fr", prompt="", offset=0.0, timeout=60,
        log=__import__("logging").getLogger("t"), vad=vad,
    )
    assert "--vad" in seen["cmd"]
    assert str(vad) in seen["cmd"]
    assert "-nt" not in seen["cmd"]      # timestamps stay on, always


def test_no_vad_means_no_vad_flags(tmp_path, monkeypatch):
    import subprocess as sp

    import src.transcribe as mod

    seen = {}

    def fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        return sp.CompletedProcess(cmd, 0, "[00:00:00.000 --> 00:00:01.000] Bonjour.\n", "")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._decode(
        tmp_path / "a.wav", binary="whisper-cli", model=tmp_path / "m.bin",
        language="fr", prompt="", offset=0.0, timeout=60,
        log=__import__("logging").getLogger("t"), vad=None,
    )
    assert "--vad" not in seen["cmd"]


def test_yield_check_accepts_a_normal_decode():
    from src.transcribe import yielded_enough

    segments = [(float(i), "un deux trois quatre cinq six") for i in range(20)]
    assert yielded_enough(segments, 60.0)


def test_yield_check_rejects_a_decode_that_returned_almost_nothing():
    """45 s of audio that decoded to "- Well there." is a wrong language pin."""
    from src.transcribe import yielded_enough

    assert not yielded_enough([(0.0, "- Well there.")], 45.0)


def test_yield_check_does_not_second_guess_a_zero_length_run():
    from src.transcribe import yielded_enough

    assert yielded_enough([], 0.0)
