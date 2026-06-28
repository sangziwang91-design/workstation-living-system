import pytest
from unittest.mock import MagicMock
from wls.provider_hub import ProviderHub, _NoRedirect
from urllib.error import HTTPError

def test_probe_redirect_blocked():
    hub = ProviderHub("/tmp/wls_test")
    opener = MagicMock()
    # Mock redirect response
    response = MagicMock()
    response.status = 302
    response.getheader.return_value = "http://169.254.169.254"
    opener.open.return_value = response

    # Actually the _NoRedirect handler should be tested
    handler = _NoRedirect()
    with pytest.raises(HTTPError, match="redirect blocked"):
        handler.redirect_request(MagicMock(), None, 302, "Found", {}, "http://unsafe.com")

test_probe_redirect_blocked()
print("REDIRECT BLOCKED TEST PASSED")
