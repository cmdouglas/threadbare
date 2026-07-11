import threadbare


def test_version_matches_installed_package_metadata():
    from importlib.metadata import version

    assert threadbare.__version__ == version("threadbare")
