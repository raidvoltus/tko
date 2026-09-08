from tko.exchange.tokocrypto import DEFAULT_RECV_WINDOW


def test_recvwindow_default_5000():
    assert DEFAULT_RECV_WINDOW == 5000
