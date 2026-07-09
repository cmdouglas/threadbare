def test_playwright_harness_works(page):
    page.set_content("<html><body><h1>threadbare</h1></body></html>")
    assert page.inner_text("h1") == "threadbare"
