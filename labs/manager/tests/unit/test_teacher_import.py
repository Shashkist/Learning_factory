from app.teacher import check_lines, split_lines


def test_split_lines_numbers_lines_as_pasted_and_skips_blanks():
    lines = split_lines("\n111, A\n\n   \n222,B\r\n")
    assert [(ln.line, ln.id, ln.name) for ln in lines] == [(2, "111", "A"), (5, "222", "B")]


def test_split_at_first_comma_keeps_the_rest_of_the_name():
    (ln,) = split_lines("123,  Fake, Surname  ")
    assert (ln.id, ln.name) == ("123", "Fake, Surname")


def test_line_without_comma_has_no_name():
    (ln,) = split_lines("123456")
    assert ln.name == ""


def codes(text):
    return {n: e["code"] for n, e in check_lines(split_lines(text)).items()}


def test_invalid_ids():
    text = "abc, N\n000, N\n٣٤٥, N\n, N\n" + "1" * 33 + ", N\n" + "7" * 32 + ", N"
    assert codes(text) == {n: "invalid_id" for n in range(1, 6)}


def test_missing_name_and_messages_never_repeat_the_id():
    errs = check_lines(split_lines("123456789\n123456780,  "))
    assert [e["code"] for e in errs.values()] == ["missing_name", "missing_name"]
    for e in errs.values():
        assert e["message"].strip() and "Line" not in e["message"]  # the UI adds the line
        assert "12345678" not in e["message"]


def test_duplicates_after_normalization_are_errors_from_the_second_line():
    assert codes("123456, A\n 0123 456, B\n123456, C") == {
        2: "duplicate_in_paste", 3: "duplicate_in_paste"}


def test_a_row_without_a_name_does_not_claim_the_id():
    assert codes("555\n555, A") == {1: "missing_name"}
