

def test_a_staged_file_is_not_treated_as_uploaded():
    """Staging means the upload FAILED and the file is sitting in _to_upload/
    waiting to be dragged in by hand. Counting it as done means the retry never
    happens and it never reaches the notebook.

    Tour_de_taille.png hit this on 2026-09-02: NotebookLM timed out while
    processing it, the run recorded mode "staging", and the next run answered
    "0 uploaded, 1 already done" and skipped it forever.
    """
    from src.notebooklm_upload import build_upload_index

    records = [
        {"course_code": "109-321-EM", "filename": "IMC.png", "mode": "auto"},
        {"course_code": "109-321-EM", "filename": "Tour_de_taille.png", "mode": "staging"},
    ]
    index = build_upload_index(records)
    assert ("109-321-EM", "IMC.png") in index
    assert ("109-321-EM", "Tour_de_taille.png") not in index, "it will never retry"


def test_a_record_with_no_mode_still_counts_as_done():
    """Older records predate the mode field and were all real uploads.
    Dropping them would re-upload the whole semester."""
    from src.notebooklm_upload import build_upload_index

    index = build_upload_index([{"course_code": "330-704-EM", "filename": "x.docx"}])
    assert ("330-704-EM", "x.docx") in index


def test_retrying_a_staged_file_replaces_rather_than_duplicates():
    """"Staged" does not reliably mean "absent from the notebook".

    Tour_de_taille.png was staged on 2026-09-02 because NotebookLM timed out
    while WAITING for it to process, and it turned out to have been in the
    notebook the whole time. A retry that simply adds would leave two copies of
    the same source, and a notebook answering from whichever it read first.
    """
    import inspect

    from src.notebooklm_upload import _drain

    source = inspect.getsource(_drain)
    assert "staged_before" in source
    assert "or key in staged_before" in source
