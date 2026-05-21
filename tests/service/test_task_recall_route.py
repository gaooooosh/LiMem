from __future__ import annotations


def test_recall_route_returns_prompt_text(client, make_user_key):
    _, token, _ = make_user_key("recall-route-user", scopes="r,w")
    db = client.post(
        "/databases",
        json={"display_name": "Recall Route"},
        headers={"X-API-Key": token},
    ).json()

    response = client.post(
        f"/db/{db['db_id']}/recall",
        json={"task": "请处理当前任务", "limit": 3, "include_debug": True},
        headers={"X-API-Key": token},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt_text"].startswith("## Relevant Memory")
    assert body["items"] == [{"kind": "Rule", "text": "fake recall"}]
    assert body["stats"]["limit"] == 3


def test_recall_route_keeps_query_route_unchanged(client, make_user_key):
    _, token, _ = make_user_key("recall-query-user", scopes="r,w")
    db = client.post(
        "/databases",
        json={"display_name": "Query Route"},
        headers={"X-API-Key": token},
    ).json()

    response = client.post(
        f"/db/{db['db_id']}/query",
        json={"query": "anything", "top_k": 2},
        headers={"X-API-Key": token},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"results": [], "total": 0}
