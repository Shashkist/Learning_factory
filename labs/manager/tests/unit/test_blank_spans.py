from app.content import _background_is_quiet, _blank_spans


def test_substitutions_and_backticks_become_one_word():
    text, holds = _blank_spans("a $(b | c) `d | e` <(f) g")
    assert text == "a xxxxxxxx xxxxxxx xxxx g"
    assert not holds


def test_arithmetic_with_a_nested_group_is_one_span():
    assert _blank_spans("h $((n*(m+1))) | t")[0] == "h xxxxxxxxxxxx | t"


def test_escaped_delimiters_are_not_delimiters_and_a_lone_close_stays():
    assert _blank_spans("a \\) b `x \\` y` )")[0] == "a xx b xxxxxxxx )"


def test_other_backslashes_stay():
    assert _blank_spans("sleep >&\\2")[0] == "sleep >&\\2"


def test_unclosed_span_stays():
    assert _blank_spans("a $( b")[0] == "a $( b"


def test_process_substitution_with_a_pipeline_holds():
    assert _blank_spans("tee >(a | b)")[1]
    assert not _blank_spans("tee >(a >x 2>&1)")[1]
    assert not _blank_spans("tee >(a || b)")[1]


def test_redirected_pipeline_in_a_process_substitution_holds_under_bash():
    job = "sleep 600 2>/dev/null | tee >(a >x 2>&1 | b >y 2>&1) >/dev/null 2>&1 "
    assert not _background_is_quiet(job, True)
    single = "sleep 600 2>/dev/null | tee >(a >x 2>&1) >/dev/null 2>&1 "
    assert _background_is_quiet(single, True)
