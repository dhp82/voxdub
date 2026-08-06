"""Các nơi dịch tương thích OpenAI, và phần đọc JSON dùng chung.

Nơi dịch nào cũng có thể trả về JSON hỏng, bị cắt giữa chừng, hoặc trả lỗi
hạn mức. Phần lớn thời gian đau đầu của người dùng nằm ở đây, nên hành vi
phải rõ ràng: lỗi nào chia đôi lô để thử lại, lỗi nào dừng ngay kèm việc cần
làm, và bản dịch phải ghép đúng câu.
"""
from __future__ import annotations

import json

import pytest

from autodub.config import Settings
from autodub.languages import get_target
from autodub.text import translate_openai as engine
from autodub.text.translate_common import (
    RateLimited, TranslateError, contains_cjk, merge_translations,
    parse_response_segments, repair_json,
)

TARGET = get_target("vi")


def settings(**kw):
    base = dict(translate_engine="openrouter",
                openrouter_api_key="k",
                openrouter_url="https://x/v1/chat/completions",
                openrouter_model="m",
                translate_max_retries=1, parallel_workers=2,
                translate_batch_size=2)
    base.update(kw)
    return Settings(**base)


def seg(i: int, text: str = "原文") -> dict:
    return {"id": i, "text": text, "start": float(i), "end": i + 1.0,
            "duration": 1.0, "slot": 1.0}


class FakeResponse:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("không phải JSON")
        return self._payload


def reply(content: str, finish: str = "stop") -> FakeResponse:
    return FakeResponse(200, {"choices": [
        {"message": {"content": content}, "finish_reason": finish}]})


def fake_post(monkeypatch, responses):
    """Thay requests.post bằng một danh sách phản hồi dựng sẵn.

    Payload được CHÉP lại chứ không giữ tham chiếu: lớp gọi sửa chính dict đó
    giữa các lần thử (hạ chế độ đầu ra chẳng hạn), giữ tham chiếu thì mọi lần
    gọi ghi lại đều trông giống nhau và bài kiểm thử thành vô nghĩa.
    """
    import copy

    calls = []
    queue = list(responses)

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": copy.deepcopy(json),
                      "headers": dict(headers or {})})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    import requests
    monkeypatch.setattr(requests, "post", _post)
    return calls


# ------------------------------------------------------------ đọc JSON --- #

def test_parses_a_plain_object():
    out = parse_response_segments('{"segments": [{"id": 1, "text_vi": "A"}]}')
    assert out == [{"id": 1, "text_vi": "A"}]


def test_parses_a_bare_array():
    assert parse_response_segments('[{"id": 1}]') == [{"id": 1}]


def test_strips_markdown_fences():
    out = parse_response_segments('```json\n{"segments": [{"id": 2}]}\n```')
    assert out == [{"id": 2}]


def test_ignores_chatter_around_the_json():
    out = parse_response_segments(
        'Here you go:\n{"segments": [{"id": 3}]}\nHope that helps!')
    assert out == [{"id": 3}]


def test_repairs_a_truncated_reply():
    """Chạm trần token thì JSON đứt ngang — phần trước đó vẫn phải cứu được."""
    out = parse_response_segments(
        '{"segments": [{"id": 1, "text_vi": "Xong."}, {"id": 2, "text_vi": "Dở')
    assert out[0] == {"id": 1, "text_vi": "Xong."}


def test_repair_closes_brackets_in_the_right_order():
    assert json.loads(repair_json('{"a": [1, 2'))["a"] == [1, 2]


def test_unreadable_reply_raises_with_a_sample():
    with pytest.raises(TranslateError, match="JSON hỏng"):
        parse_response_segments("hoàn toàn không phải JSON")


# --------------------------------------------------------------- ghép ---- #

def test_merge_fills_the_translated_field():
    merged = merge_translations([seg(1)], [{"id": 1, "text_vi": "Chào"}],
                                "text_vi")
    assert merged[0]["text_vi"] == "Chào."          # tự thêm dấu kết câu
    assert merged[0]["start"] == 1.0                # giữ nguyên mốc thời gian


def test_merge_does_not_touch_the_input():
    original = seg(1)
    merge_translations([original], [{"id": 1, "text_vi": "Chào"}], "text_vi")
    assert "text_vi" not in original


def test_merge_reports_missing_segments():
    with pytest.raises(TranslateError, match="thiếu 1 câu"):
        merge_translations([seg(1), seg(2)],
                           [{"id": 1, "text_vi": "A"}], "text_vi")


def test_merge_accepts_positional_replies_without_ids():
    """Mô hình quên id nhưng trả đủ và đúng thứ tự thì vẫn dùng được."""
    merged = merge_translations([seg(1), seg(2)],
                                [{"text_vi": "A"}, {"text_vi": "B"}],
                                "text_vi")
    assert [m["text_vi"] for m in merged] == ["A.", "B."]


def test_contains_cjk():
    assert contains_cjk("còn 原文 sót lại")
    assert not contains_cjk("thuần tiếng Việt")


# ------------------------------------------------------------ gọi mạng --- #

def test_missing_key_stops_before_any_request(monkeypatch):
    calls = fake_post(monkeypatch, [reply("{}")])
    with pytest.raises(TranslateError, match="Chưa có API Key"):
        engine.chat(settings(openrouter_api_key=""), "openrouter", "s", "u", 1)
    assert calls == []


def test_missing_url_or_model_stops_early(monkeypatch):
    fake_post(monkeypatch, [reply("{}")])
    with pytest.raises(TranslateError, match="địa chỉ máy chủ hoặc tên mô hình"):
        engine.chat(settings(openrouter_model=""), "openrouter", "s", "u", 1)


def test_rate_limit_raises_its_own_error(monkeypatch):
    fake_post(monkeypatch, [FakeResponse(429, {}, "quota")])
    with pytest.raises(RateLimited, match="hết hạn mức"):
        engine.chat(settings(), "openrouter", "s", "u", 1)


def test_bad_key_is_reported_immediately(monkeypatch):
    calls = fake_post(monkeypatch, [FakeResponse(401, {}, "nope")])
    with pytest.raises(TranslateError, match="không hợp lệ hoặc đã hết hạn"):
        engine.chat(settings(translate_max_retries=3), "openrouter", "s", "u", 3)
    assert len(calls) == 1          # không thử lại một API Key sai


def test_unknown_model_is_reported_immediately(monkeypatch):
    fake_post(monkeypatch, [FakeResponse(404, {}, "no model")])
    with pytest.raises(TranslateError, match="không có mô hình"):
        engine.chat(settings(), "openrouter", "s", "u", 1)


def test_truncated_reply_is_an_error_not_a_silent_loss(monkeypatch):
    fake_post(monkeypatch, [reply('{"segments": [', finish="length")])
    with pytest.raises(TranslateError, match="cắt phản hồi giữa chừng"):
        engine.chat(settings(), "openrouter", "s", "u", 1)


def test_schema_rejection_falls_back_to_plain_json_mode(monkeypatch):
    calls = fake_post(monkeypatch, [FakeResponse(400, {}, "no schema"),
                                    reply("ok")])
    out = engine.chat(settings(), "openrouter", "s", "u", 2,
                      schema={"type": "object"})
    assert out == "ok"
    assert calls[0]["json"]["response_format"]["type"] == "json_schema"
    assert calls[1]["json"]["response_format"]["type"] == "json_object"


def test_anthropic_sends_both_auth_headers(monkeypatch):
    calls = fake_post(monkeypatch, [reply("ok")])
    engine.chat(settings(translate_engine="anthropic", anthropic_api_key="k2",
                         anthropic_url="https://a/v1/chat/completions",
                         anthropic_model="m"),
                "anthropic", "s", "u", 1)
    headers = calls[0]["headers"]
    assert headers["Authorization"] == "Bearer k2"
    assert headers["x-api-key"] == "k2"
    assert "anthropic-version" in headers


def test_each_engine_uses_its_own_url(monkeypatch):
    calls = fake_post(monkeypatch, [reply("ok")])
    engine.chat(settings(translate_engine="deepseek", deepseek_api_key="k",
                         deepseek_url="https://deepseek/v1/chat/completions",
                         deepseek_model="deepseek-chat"),
                "deepseek", "s", "u", 1)
    assert calls[0]["url"] == "https://deepseek/v1/chat/completions"
    assert calls[0]["json"]["model"] == "deepseek-chat"


# --------------------------------------------------------------- dịch ---- #

def test_translate_segments_covers_every_batch(monkeypatch):
    def _post(url, json=None, headers=None, timeout=None):
        payload = json["messages"][1]["content"]
        ids = [s["id"] for s in _parse_ids(payload)]
        return reply(_json_segments(ids))

    import requests
    monkeypatch.setattr(requests, "post", _post)

    segments = [seg(i) for i in range(1, 6)]
    out = engine.translate_segments(segments, TARGET, "zh-CN",
                                    settings(), engine="openrouter")
    assert [s["id"] for s in out] == [1, 2, 3, 4, 5]
    assert all(s["text_vi"] for s in out)


def test_a_choking_batch_is_halved(monkeypatch):
    seen = []

    def _post(url, json=None, headers=None, timeout=None):
        ids = [s["id"] for s in _parse_ids(json["messages"][1]["content"])]
        seen.append(len(ids))
        if len(ids) > 1:
            return reply("hỏng không phải JSON")
        return reply(_json_segments(ids))

    import requests
    monkeypatch.setattr(requests, "post", _post)

    out = engine.translate_segments([seg(1), seg(2)], TARGET, "zh-CN",
                                    settings(), engine="openrouter")
    assert [s["id"] for s in out] == [1, 2]
    assert 1 in seen              # đã chia nhỏ tới từng câu


def test_rate_limit_is_never_split(monkeypatch):
    calls = fake_post(monkeypatch, [FakeResponse(429, {}, "quota")])
    with pytest.raises(RateLimited):
        engine.translate_segments([seg(1), seg(2)], TARGET, "zh-CN",
                                  settings(), engine="openrouter")
    # Chia đôi khi bị giới hạn tốc độ chỉ làm số request tăng lên.
    assert len(calls) <= 2


def _parse_ids(payload: str) -> list[dict]:
    start = payload.rindex("[")
    return json.loads(payload[start:])


def _json_segments(ids: list[int]) -> str:
    return json.dumps({"segments": [{"id": i, "text_vi": f"Câu {i}."}
                                    for i in ids]}, ensure_ascii=False)


# ------------------------------------------------------ sổ dịch tạm ---- #

def _ok_post(monkeypatch):
    """requests.post luôn dịch đúng, đồng thời đếm số lần gọi."""
    calls = []

    def _post(url, json=None, headers=None, timeout=None):
        ids = [s["id"] for s in _parse_ids(json["messages"][1]["content"])]
        calls.append(ids)
        return reply(_json_segments(ids))

    import requests
    monkeypatch.setattr(requests, "post", _post)
    return calls


def test_checkpoint_is_discarded_after_full_success(monkeypatch, tmp_path):
    _ok_post(monkeypatch)
    ckpt = str(tmp_path / "ckpt.json")
    out = engine.translate_segments([seg(1), seg(2), seg(3)], TARGET, "zh-CN",
                                    settings(), engine="openrouter",
                                    checkpoint_path=ckpt)
    assert len(out) == 3
    import os
    assert not os.path.exists(ckpt)   # dịch trọn vẹn thì sổ tự xóa


def test_resume_skips_batches_already_in_checkpoint(monkeypatch, tmp_path):
    # Lượt 1: lô thứ hai (id 3, 4) hỏng vì hết hạn mức — lô 1 đã kịp lưu sổ.
    def _post_fail_second(url, json=None, headers=None, timeout=None):
        ids = [s["id"] for s in _parse_ids(json["messages"][1]["content"])]
        if 3 in ids:
            return FakeResponse(429, {}, "quota")
        return reply(_json_segments(ids))

    import requests
    monkeypatch.setattr(requests, "post", _post_fail_second)

    ckpt = str(tmp_path / "ckpt.json")
    segments = [seg(1), seg(2), seg(3), seg(4)]
    with pytest.raises(RateLimited):
        engine.translate_segments(segments, TARGET, "zh-CN",
                                  settings(parallel_workers=1),
                                  engine="openrouter", checkpoint_path=ckpt)
    import os
    assert os.path.exists(ckpt)       # phần đã dịch không bị vứt đi

    # Lượt 2: chỉ lô còn thiếu mới gọi API.
    calls = _ok_post(monkeypatch)
    out = engine.translate_segments(segments, TARGET, "zh-CN",
                                    settings(), engine="openrouter",
                                    checkpoint_path=ckpt)
    assert [s["id"] for s in out] == [1, 2, 3, 4]
    assert all(s["text_vi"] for s in out)
    assert calls == [[3, 4]]          # lô 1 lấy từ sổ, không dịch lại
    assert not os.path.exists(ckpt)


def test_checkpoint_ignores_entries_whose_source_changed(monkeypatch, tmp_path):
    from autodub.text.translate_common import TranslateCheckpoint

    ckpt = str(tmp_path / "ckpt.json")
    cp = TranslateCheckpoint(ckpt, "text_vi")
    cp.put([{**seg(1), "text_vi": "Bản cũ."}])

    # Câu gốc đã bị sửa (nghe lại, sửa tay) — bản dịch cũ không dùng được.
    changed = seg(1, text="新的原文")
    assert TranslateCheckpoint(ckpt, "text_vi").take([changed]) is None
    # Câu gốc giữ nguyên thì vẫn lấy được.
    same = TranslateCheckpoint(ckpt, "text_vi").take([seg(1)])
    assert same is not None and same[0]["text_vi"] == "Bản cũ."


def test_corrupt_checkpoint_falls_back_to_full_translate(monkeypatch, tmp_path):
    ckpt = tmp_path / "ckpt.json"
    ckpt.write_text("{hỏng", encoding="utf-8")
    calls = _ok_post(monkeypatch)
    out = engine.translate_segments([seg(1), seg(2)], TARGET, "zh-CN",
                                    settings(), engine="openrouter",
                                    checkpoint_path=str(ckpt))
    assert [s["id"] for s in out] == [1, 2]
    assert calls                       # sổ hỏng thì dịch lại, không sập


# --------------------------------------------------------- đếm token ---- #

def test_usage_is_counted_from_the_response(monkeypatch):
    from autodub.text.translate_common import USAGE

    def _post(url, json=None, headers=None, timeout=None):
        ids = [s["id"] for s in _parse_ids(json["messages"][1]["content"])]
        resp = reply(_json_segments(ids))
        resp._payload["usage"] = {"prompt_tokens": 100, "completion_tokens": 40}
        return resp

    import requests
    monkeypatch.setattr(requests, "post", _post)

    USAGE.reset()
    engine.translate_segments([seg(1), seg(2), seg(3)], TARGET, "zh-CN",
                              settings(), engine="openrouter")
    snap = USAGE.snapshot()
    assert snap["requests"] == 2              # 3 câu, mỗi lô 2 câu → 2 lượt
    assert snap["prompt_tokens"] == 200
    assert snap["completion_tokens"] == 80
    assert snap["total_tokens"] == 280
    USAGE.reset()


def test_usage_missing_in_response_is_not_an_error(monkeypatch):
    from autodub.text.translate_common import USAGE

    _ok_post(monkeypatch)                      # reply() không có "usage"
    USAGE.reset()
    out = engine.translate_segments([seg(1)], TARGET, "zh-CN",
                                    settings(), engine="openrouter")
    assert out[0]["text_vi"]
    assert USAGE.snapshot()["requests"] == 0
