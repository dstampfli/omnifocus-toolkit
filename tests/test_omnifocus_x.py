from omnifocus_x import extract_tweet_ids, XPostFetcher


def test_extract_x_com_status():
    assert extract_tweet_ids("see https://x.com/jack/status/20") == ["20"]


def test_extract_twitter_com_with_query():
    assert extract_tweet_ids("https://twitter.com/a/status/456?s=20&t=x") == ["456"]


def test_extract_mobile_and_trailing_slash():
    assert extract_tweet_ids("https://mobile.twitter.com/u/status/789/") == ["789"]


def test_extract_multiple_dedupe_preserves_order():
    text = ("x.com/a/status/111 and x.com/b/status/222 "
            "and again x.com/c/status/111")
    assert extract_tweet_ids(text) == ["111", "222"]


def test_extract_ignores_non_x_hosts():
    # 'box.com' must not match via the 'x.com' substring.
    assert extract_tweet_ids("https://box.com/a/status/999") == []
    assert extract_tweet_ids("https://example.com/status/5") == []


def test_extract_empty_and_none_safe():
    assert extract_tweet_ids("") == []
    assert extract_tweet_ids(None) == []


def test_fetcher_no_token_returns_empty_and_never_fetches():
    calls = []
    f = XPostFetcher("", 25, fetch_fn=lambda tid, tok: calls.append(tid) or "x")
    assert f.texts_for("x.com/a/status/1") == []
    assert calls == []


def test_fetcher_returns_texts_and_dedupes(monkeypatch):
    calls = []
    def fake(tid, tok):
        calls.append(tid)
        return f"POST {tid}"
    f = XPostFetcher("tok", 25, fetch_fn=fake)
    out = f.texts_for("x.com/a/status/1 x.com/b/status/2 x.com/c/status/1")
    assert out == ["POST 1", "POST 2"]              # each unique id once
    assert calls == ["1", "2"]                       # fetched once per unique id
    assert f.used == 2


def test_fetcher_honors_cap(monkeypatch):
    calls = []
    f = XPostFetcher("tok", 1, fetch_fn=lambda tid, tok: calls.append(tid) or f"P{tid}")
    out = f.texts_for("x.com/a/status/1 x.com/b/status/2")
    assert out == ["P1"]        # second id skipped: cap reached
    assert calls == ["1"]
    assert f.used == 1


def test_fetcher_skips_none_results():
    f = XPostFetcher("tok", 25, fetch_fn=lambda tid, tok: None)
    assert f.texts_for("x.com/a/status/1") == []
    assert f.used == 1          # a None result still counts as a used lookup


def test_fetcher_serializes_concurrent_lookups():
    # The reviewer now runs task reviews in parallel threads that share one
    # run-scoped fetcher, so texts_for must be mutually exclusive: two lookups
    # must never run at once (which would race the check-then-increment on the
    # use cap). With a lock the observed concurrency stays at 1.
    import threading
    import time

    cur = {"n": 0, "max": 0}
    probe = threading.Lock()

    def fake(tid, tok):
        with probe:
            cur["n"] += 1
            cur["max"] = max(cur["max"], cur["n"])
        time.sleep(0.05)                 # hold the "in-fetch" window open
        with probe:
            cur["n"] -= 1
        return f"P{tid}"

    f = XPostFetcher("tok", 100, fetch_fn=fake)
    threads = [threading.Thread(target=lambda i=i: f.texts_for(f"x.com/a/status/{i}"))
               for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert cur["max"] == 1
