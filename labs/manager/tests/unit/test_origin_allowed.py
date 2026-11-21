import pytest

from app.term_proxy import origin_allowed

PUB = "https://labs.shashkist.com"


@pytest.mark.parametrize("pub", [None, "", "  "])
def test_no_setting_means_no_check(pub):
    assert origin_allowed("https://evil.example", pub)


def test_no_origin_header_is_allowed():
    assert origin_allowed(None, PUB)


@pytest.mark.parametrize("o", [PUB, "HTTPS://Labs.Shashkist.com"])
@pytest.mark.parametrize("pub", [PUB, PUB + "/"])
def test_match_ignores_case_and_trailing_slash(o, pub):
    assert origin_allowed(o, pub)


@pytest.mark.parametrize(
    "o",
    ["null", "", "https://evil.example", "http://labs.shashkist.com",
     "https://x.shashkist.com", "https://labs.shashkist.com:8443"],
)
def test_other_origins_are_refused(o):
    assert not origin_allowed(o, PUB)
