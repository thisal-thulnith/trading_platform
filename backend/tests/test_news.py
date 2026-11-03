from app.news.feeds import classify_event, extract_coins, impact, jaccard, sentiment


def test_coin_extraction():
    assert extract_coins("Bitcoin and Ethereum ETFs see inflows") == ["BTC", "ETH"]
    assert "SOL" in extract_coins("Solana network upgrade")
    assert extract_coins("Bitcoin Cash rallies") == ["BCH"]
    assert extract_coins("The weather is nice") == []


def test_event_and_sentiment():
    assert classify_event("Exchange hacked, $40M drained") == "hack_exploit"
    assert classify_event("SEC approves spot ETF") in ("regulation", "etf")
    assert sentiment("Bitcoin surges to record high as inflows jump") > 0
    assert sentiment("Protocol exploit: funds stolen, token plunges") < 0
    assert sentiment("Weekly update") == 0


def test_impact_and_similarity():
    assert impact("hack_exploit", 0.8, 1, ["ETH"]) == "high"
    assert impact("hack_exploit", 0.8, 1, []) != "high"
    assert impact("general", 0.5, 1) == "low"
    assert jaccard("Bitcoin hits record high", "Bitcoin hits a new record high") > 0.5
