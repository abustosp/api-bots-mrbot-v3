from urllib.parse import parse_qs, urlparse

from central_api.storage import presign_get_url


def _url(filename):
    return presign_get_url(
        endpoint="https://s3.example", region="us-east-1", bucket="b",
        access_key="a", secret_key="s", object_key="jobs/1/x", filename=filename,
    )


def test_url_incluye_nombre_original_firmado():
    qs = parse_qs(urlparse(_url("MCE - Ñandú S.A.csv")).query)
    cd = qs["response-content-disposition"][0]
    assert cd.startswith("attachment;")
    assert "filename*=UTF-8''MCE%20-%20%C3%91and%C3%BA%20S.A.csv" in cd


def test_sin_nombre_no_agrega_disposition():
    assert "response-content-disposition" not in _url(None)
    assert "response-content-disposition" not in _url("  ")
