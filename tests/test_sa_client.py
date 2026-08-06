import sa_client


def test_exports_searchatlas_class():
    assert hasattr(sa_client.SearchAtlas, "get")
    assert hasattr(sa_client.SearchAtlas, "paginate")


def test_exports_error_type():
    assert issubclass(sa_client.SearchAtlasError, Exception)


def test_brand_tokens_includes_bare_and_suffixed_forms():
    toks = sa_client.brand_tokens("golfcourseprint.com", "Golf Course Print")
    assert "golfcourseprint" in toks


def test_generic_category_phrase_is_not_branded():
    """The hard case: a brand whose name is its own category. A lowercase,
    space-separated generic phrase must NOT be classified as branded."""
    toks = sa_client.brand_tokens("golfcourseprint.com", "Golf Course Print")
    assert sa_client.is_branded("custom golf course prints", toks) is None


def test_contiguous_brand_token_is_branded():
    toks = sa_client.brand_tokens("golfcourseprint.com", "Golf Course Print")
    assert sa_client.is_branded("GolfCoursePrint.com reviews", toks) is not None


def test_capitalised_run_is_branded():
    toks = sa_client.brand_tokens("golfcourseprint.com", "Golf Course Print")
    assert sa_client.is_branded("Golf Course Print pricing", toks) is not None


def test_unrelated_query_is_not_branded():
    toks = sa_client.brand_tokens("getpetermd.com", "PeterMD")
    assert sa_client.is_branded("trt cost", toks) is None


def test_sa_root_points_at_an_existing_directory():
    import os
    assert os.path.isdir(sa_client.SA_ROOT)


def test_sa_root_after_repo_root_in_sys_path():
    """Verify that SA_ROOT is appended (not inserted at 0) so this repo's
    modules take precedence over same-named modules in the sibling project."""
    import os
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(sa_client.__file__)))
    sa_root_idx = sys.path.index(sa_client.SA_ROOT)

    if repo_root in sys.path:
        repo_root_idx = sys.path.index(repo_root)
        assert sa_root_idx > repo_root_idx, \
            "SA_ROOT should appear after repo root in sys.path"
    else:
        assert sa_root_idx > 0, \
            "SA_ROOT should not be at index 0 (repo root not in sys.path)"
