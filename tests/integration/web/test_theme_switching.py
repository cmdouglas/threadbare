def test_board_index_links_default_theme_stylesheet_when_no_cookie_or_query(client):
    resp = client.get("/")

    assert b"theme-subsilver.css" in resp.data
    assert b"theme-plain.css" not in resp.data


def test_query_param_theme_sets_cookie_and_switches_stylesheet(client):
    resp = client.get("/?theme=plain")

    assert b"theme-plain.css" in resp.data
    set_cookie_headers = resp.headers.get_all("Set-Cookie")
    assert any("theme=plain" in header for header in set_cookie_headers)


def test_cookie_alone_persists_theme_choice_without_query_param(client):
    client.set_cookie("theme", "plain")

    resp = client.get("/")

    assert b"theme-plain.css" in resp.data
    assert "Set-Cookie" not in resp.headers


def test_invalid_query_param_theme_falls_back_to_default_and_does_not_set_cookie(client):
    resp = client.get("/?theme=bogus")

    assert b"theme-subsilver.css" in resp.data
    assert "Set-Cookie" not in resp.headers
