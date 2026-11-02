import pytest
from google.ads.googleads.client import GoogleAdsClient
from google.oauth2.credentials import Credentials


@pytest.fixture(scope="session")
def client():
    """Offline client. Used only for protobuf types and resource name helpers.

    A credentials object that already holds a token never triggers a refresh,
    so no network call is made.
    """
    creds = Credentials(token="dummy-access-token")
    return GoogleAdsClient(credentials=creds, developer_token="dummy", use_proto_plus=True)


CID = "1234567890"
