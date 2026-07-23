def test_board_index_shows_avatars_by_default(client):
    resp = client.get("/")

    assert b"Hide avatars" in resp.data


def test_query_param_off_sets_cookie_and_switches_link_label(client):
    resp = client.get("/?avatars=off")

    assert b"Show avatars" in resp.data
    set_cookie_headers = resp.headers.get_all("Set-Cookie")
    assert any("show_avatars=off" in header for header in set_cookie_headers)


def test_cookie_alone_persists_the_choice_without_query_param(client):
    client.set_cookie("show_avatars", "off")

    resp = client.get("/")

    assert b"Show avatars" in resp.data
    assert "Set-Cookie" not in resp.headers


def test_invalid_query_param_falls_back_to_default_and_does_not_set_cookie(client):
    resp = client.get("/?avatars=bogus")

    assert b"Hide avatars" in resp.data
    assert "Set-Cookie" not in resp.headers
