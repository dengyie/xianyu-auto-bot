"""滑块验证统计 range 过滤的 datetime 路径回归（production review 补测）。

`get_slider_verification_session_stats` 的 `_build_range_filter` 曾因模块缺
`from datetime import datetime, timedelta, timezone` 在 range_key=today/7d 时
NameError -> 500。本测试锁住 today/7d/all 三条路径均可正常返回。
"""


def test_slider_stats_range_paths_ok(client, auth):
    for range_key in ("today", "7d", "all"):
        resp = client.get(
            f"/admin/slider-verification-stats?range_key={range_key}",
            headers=auth,
        )
        assert resp.status_code == 200, (range_key, resp.text[:200])
        payload = resp.json()
        assert payload.get("success") is True
        data = payload["data"]
        assert "total_sessions" in data
        assert data.get("selected_range") == range_key
