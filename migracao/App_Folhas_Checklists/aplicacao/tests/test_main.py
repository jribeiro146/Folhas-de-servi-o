import src.main as main


def test_pick_port_skips_busy_ports(monkeypatch):
    busy_ports = {5001, 5002}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def setsockopt(self, *args, **kwargs):
            return None

        def connect_ex(self, address):
            return 0 if address[1] in busy_ports else 1

    monkeypatch.setattr(main.socket, "socket", lambda *args, **kwargs: FakeSocket())

    assert main.pick_port(start_port=5001, max_attempts=5) == 5003
