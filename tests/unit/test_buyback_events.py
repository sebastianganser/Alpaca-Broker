"""Unit tests for the 8-K buyback authorization classifier (step D1)."""

from datetime import date

import pytest

from trading_signals.collectors.buyback_events import (
    classify_buyback_text,
    html_to_text,
    is_authorization_sentence,
    month_windows,
    parse_amount,
    parse_fts_hit,
    split_sentences,
)

# Real sentences from 8-K filings (Feb 2024), shortened where marked.
POSITIVE = [
    "On January 31, 2024, the Board of Directors (the “Board”) of Blue Bird "
    "Corporation (the “Company”) authorized and approved a share repurchase "
    "program for up to $60 million of the currently outstanding shares of the "
    "Company’s common stock over a period of 24 months.",
    "ChampionX announces that our Board of Directors approved an increase to "
    "our share repurchase program (the “Share Repurchase Program”).",
    "On February 2, 2024, the Board of Directors of the Company approved a new "
    "share repurchase program authorizing the Company to purchase up to $200 "
    "million of the Company’s common stock over a three-year period.",
    "In January 2024, Fortinet’s board of directors authorized a $500.0 million "
    "increase in the authorized share repurchase amount under our share "
    "repurchase program.",
    "We also announced authorization of a new $1 billion share repurchase "
    "program and a 6% increase to our quarterly dividend.",
    "CDW Authorizes $750 Million Share Repurchase Program Increase and Declares "
    "Quarterly Cash Dividend of $0.62 Per Share",
]

NEGATIVE = [
    # execution report
    "Share Repurchase Program During the fourth quarter of 2023, we repurchased "
    "1,290,639 shares of our common stock for a total of $175 million.",
    # benefit mention in an earnings release
    "Adjusted earnings per share were $3.03, up 8% compared to a year ago, "
    "primarily reflecting higher earnings and the benefit of our share "
    "repurchase program.",
    # boilerplate
    "Future dividends and share repurchase authorizations will be subject to "
    "approval by CDW's Board of Directors.",
    # remaining capacity
    "As of December 31, 2023, approximately $2.1 billion remained available "
    "under the share repurchase program approved by the Board in 2022.",
    # previously authorized
    "The repurchases were made under the $5 billion program previously "
    "authorized by the Board.",
    # debt repurchase
    "The Board authorized the repurchase of up to $500 million of the "
    "Company's outstanding senior notes.",
    # no authorization verb
    "Cash paid for share repurchases of $1.50 billion.",
    # audit sample: execution with "purchased"
    "During the year ended December 31, 2023, we purchased 1,265,661 shares of "
    "our common stock under the approved share repurchase program at a cost of "
    "$438.3 million, or an average price of $346.34 per share.",
    # audit sample: restatement of an existing programme
    "As previously reported, under News Corporation's stock repurchase program, "
    "the Company is authorized to acquire from time to time up to $1 billion in "
    "the aggregate of the Company's outstanding shares.",
]


def test_stale_sentence_ignored_with_filing_date():
    s = ("In January 2023, our Board of Directors authorized a $1.0 billion share "
         "repurchase program.")
    assert is_authorization_sentence(s)  # without date context
    assert not is_authorization_sentence(s, as_of=date(2024, 2, 1))
    # December authorization reported in early January stays valid
    dec = ("On December 14, 2023, the Board approved a new $500 million share "
           "repurchase program.")
    assert is_authorization_sentence(dec, as_of=date(2024, 1, 5))
    result = classify_buyback_text(s + " " + POSITIVE[2], as_of=date(2024, 2, 7))
    assert result.snippet.startswith("On February 2, 2024")


@pytest.mark.parametrize("sentence, as_of, expected", [
    # restated in a later earnings release -> history
    ("In December 2023, the Board of Directors refreshed the Company’s share "
     "repurchase authorization, providing for the repurchase of up to $1.5 billion "
     "of the Company’s common stock.", date(2024, 2, 13), False),
    ("Also in December, the company announced that the Board authorized the "
     "repurchase of an additional $3 billion of the company's common stock.",
     date(2024, 2, 2), False),
    ("On Nov. 27, 2023, the Company announced that the Board of Directors approved "
     "a share repurchase program authorizing the Company to repurchase up to $3.0 "
     "billion of the Company’s common stock through Dec. 26, 2026.",
     date(2024, 2, 14), False),  # first date decides, not the end date
    # sentence-initial month without year (full-run audit)
    ("In May, the Owens Corning Board of Directors approved a new share repurchase "
     "authorization for up to 12 million shares of the company’s common stock.",
     date(2025, 8, 6), False),
    ("During the second quarter of 2026, the Company continued repurchasing shares "
     "under its $5.0 billion common stock repurchase authorization.",
     date(2026, 7, 16), False),
    # recent month without day stays valid
    ("In January 2024, our Board of Directors authorized a new $1.0 billion share "
     "repurchase program, which we expect to complete by December 31, 2024.",
     date(2024, 2, 1), True),
])
def test_month_references(sentence, as_of, expected):
    assert is_authorization_sentence(sentence, as_of=as_of) is expected


def test_long_merged_sentence_rejected():
    s = ("Board approved a new $1 billion share repurchase program " + "x " * 400)
    assert not is_authorization_sentence(s)


@pytest.mark.parametrize("sentence", POSITIVE)
def test_positive_sentences(sentence):
    assert is_authorization_sentence(sentence)


@pytest.mark.parametrize("sentence", NEGATIVE)
def test_negative_sentences(sentence):
    assert not is_authorization_sentence(sentence)


@pytest.mark.parametrize("sentence, amount", [
    ("up to $60 million of shares", 60e6),
    ("a $1 billion program", 1e9),
    ("a $1,500 million program", 1.5e9),
    ("a $500.0 million increase", 500e6),
    ("approves $1B share repurchase program", 1e9),
    ("a $750M buyback", 750e6),
    ("no amount here", None),
])
def test_parse_amount(sentence, amount):
    assert parse_amount(sentence) == amount


def test_classify_document_picks_first_authorization_sentence():
    text = " ".join([NEGATIVE[0], NEGATIVE[1], POSITIVE[2], POSITIVE[0]])
    result = classify_buyback_text(text)
    assert result.is_event
    assert result.amount_usd == 200e6
    assert result.snippet.startswith("On February 2, 2024")


def test_classify_document_without_event():
    result = classify_buyback_text(" ".join(NEGATIVE))
    assert not result.is_event and result.amount_usd is None


def test_split_sentences_handles_bullets():
    parts = split_sentences("Highlights • Sales rose. • Board approved X. Next one.")
    assert parts == ["Highlights", "Sales rose.", "Board approved X.", "Next one."]


def test_html_to_text_strips_tags_and_scripts():
    html = "<html><script>x=1</script><p>Board&nbsp;approved</p><p>a  plan.</p></html>"
    assert html_to_text(html) == "Board approved a plan."
    assert html_to_text("plain   text") == "plain text"


def test_parse_fts_hit():
    raw = {
        "_id": "0001108524-24-000002:crm-20240228.htm",
        "_source": {
            "ciks": ["0001108524"], "file_date": "2024-02-28", "form": "8-K",
            "file_type": "8-K", "items": ["2.02", "8.01"],
            "display_names": ["Salesforce, Inc.  (CRM)  (CIK 0001108524)"],
        },
    }
    hit = parse_fts_hit(raw)
    assert hit.accession_number == "0001108524-24-000002"
    assert hit.document == "crm-20240228.htm"
    assert hit.cik == "0001108524" and hit.file_date == date(2024, 2, 28)
    assert hit.items == ("2.02", "8.01")
    assert parse_fts_hit({"_id": "no-colon", "_source": {}}) is None
    assert parse_fts_hit({"_id": "a:b", "_source": {"ciks": ["1"], "file_date": "x"}}) is None


def test_month_windows():
    wins = list(month_windows(date(2024, 1, 15), date(2024, 3, 10)))
    assert wins == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 10)),
    ]
