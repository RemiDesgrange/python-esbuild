import esbuild_bin


def test_import() -> None:
    assert isinstance(esbuild_bin.__version__, str)
