import pytest

from rooted.fonts import normalize_font_name

CASES = [
    ("ABCDEF+Montserrat-SemiBoldItalic", ("Montserrat", 600, True)),
    ("Montserrat-Regular", ("Montserrat", 400, False)),
    ("Arial-BoldMT", ("Arial", 700, False)),
    ("ArialMT", ("Arial", 400, False)),
    ("Arial,Bold", ("Arial", 700, False)),
    ("Arial Bold Italic", ("Arial", 700, True)),
    ("ArialNarrow-Bold", ("Arial Narrow", 700, False)),
    ("TimesNewRomanPSMT", ("Times New Roman", 400, False)),
    ("TimesNewRomanPS-BoldItalicMT", ("Times New Roman", 700, True)),
    ("Times-Roman", ("Times", 400, False)),
    ("Times New Roman", ("Times New Roman", 400, False)),
    ("Helvetica-Light", ("Helvetica", 300, False)),
    ("HelveticaNeue-Bold", ("Helvetica Neue", 700, False)),
    ("HelveticaNeueLTStd-Roman", ("Helvetica Neue", 400, False)),
    ("Gotham-Book", ("Gotham", 400, False)),
    ("Gotham-Black", ("Gotham", 900, False)),
    ("Gotham-Medium", ("Gotham", 500, False)),
    ("Calibri-Light", ("Calibri", 300, False)),
    ("Courier-Oblique", ("Courier", 400, True)),
    ("Georgia-BoldItalic", ("Georgia", 700, True)),
    ("OpenSans-ExtraBold", ("Open Sans", 800, False)),
    ("Lato-Hairline", ("Lato", 100, False)),
    ("Lato Thin Italic", ("Lato", 100, True)),
    ("SourceSansPro-Semibold", ("Source Sans", 600, False)),
    ("IBMPlexSans-Medium", ("IBM Plex Sans", 500, False)),
    ("MinionPro-It", ("Minion", 400, True)),
    ("MinionPro-BoldIt", ("Minion", 700, True)),
    ("Montserrat-ExtraLightItalic", ("Montserrat", 200, True)),
    ("PlayfairDisplay-Black", ("Playfair Display", 900, False)),
    ("Oswald-DemiBold", ("Oswald", 600, False)),
    ("Roboto", ("Roboto", 400, False)),
    ("Ultra", ("Ultra", 400, False)),  # a family named like a style word is kept
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_normalize(raw, expected):
    assert normalize_font_name(raw) == expected


def test_case_count():
    assert len(CASES) >= 20
