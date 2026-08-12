from app import flow_credentials


def test_asap_credentials_are_encrypted_and_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(flow_credentials.platform, "system", lambda: "Windows")
    monkeypatch.setattr(flow_credentials, "_dpapi", lambda data, protect: bytes(value ^ 0xA5 for value in data))

    result = flow_credentials.save_asap_credentials("portal-user", "literal-@-password", tmp_path)
    raw = flow_credentials.credential_path(tmp_path).read_text(encoding="utf-8")

    assert result["configured"] is True
    assert "portal-user" not in raw
    assert "literal-@-password" not in raw
    assert flow_credentials.load_asap_credentials(tmp_path) | {"updated_at": None} == {
        "username": "portal-user",
        "password": "literal-@-password",
        "updated_at": None,
    }
