def test_placeholder_page_renders(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Event Playlist" in response.text
