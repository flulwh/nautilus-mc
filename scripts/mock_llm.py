"""本地 mock 的 OpenAI 兼容服务，用来在不花钱、不联网的情况下验证面板的 AI 链路。

它实现最小可用的 ``POST /v1/chat/completions``（含 stream），行为规则：

* 对话里还没有工具返回 -> 先要求调用 ``list_servers``（验证工具调用通路）
* 已经有工具返回 -> 回一段总结文字（验证流式文本与收尾）

用法：

    python scripts/mock_llm.py --port 8899
    # 然后在面板「系统设置 -> AI 助手」填：
    #   base_url = http://127.0.0.1:8899/v1
    #   model    = mock
    #   api_key  = anything

注意这只是开发/演示用的桩，不要放到生产环境。
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="mock OpenAI")

TOOL_NAME = "list_servers"


def _plan(messages: list[dict]) -> tuple[str, dict | None]:
    """返回 (文本, 工具调用)。

    只看**最后一条**消息：如果刚拿到工具结果，就收尾；否则按用户这句话决定
    要不要调工具。多轮对话里这样才符合真实模型的行为。
    """
    last = messages[-1] if messages else {}
    if last.get("role") == "tool":
        return "执行完了，还需要我做别的吗？", None

    prompt = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            prompt = str(message.get("content") or "")
            break
    if "你好" in prompt or "hello" in prompt.lower():
        return "你好，我是这台机器的运维助手。", None
    # 带“强杀/kill”的请求走危险工具，用来演示/验证审批弹窗
    if "强杀" in prompt or "kill" in prompt.lower():
        return "", {"name": "server_kill", "arguments": {"server_id": 1}}
    return "", {"name": TOOL_NAME, "arguments": {}}


def _chunk(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _answer(messages: list[dict]) -> dict:
    text, call = _plan(messages)
    if call:
        return {"text": "", "tool": call}
    return {"text": text, "tool": None}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    plan = _answer(messages)
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock"),
    }

    if not body.get("stream"):
        if plan["tool"]:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": plan["tool"]["name"],
                            "arguments": json.dumps(plan["tool"]["arguments"]),
                        },
                    }
                ],
            }
            return JSONResponse({**base, "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}]})
        message = {"role": "assistant", "content": plan["text"]}
        return JSONResponse({**base, "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})

    async def stream():
        # OpenAI 的流式格式：role 先到，随后是 content / tool_calls 增量，最后 [DONE]
        yield _chunk({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
        if plan["tool"]:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            yield _chunk(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": call_id,
                                        "type": "function",
                                        "function": {"name": plan["tool"]["name"], "arguments": ""},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
            yield _chunk(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": json.dumps(plan["tool"]["arguments"])}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
            yield _chunk({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            for piece in _split(plan["text"]):
                yield _chunk({**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
            yield _chunk({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


def _split(text: str, size: int = 6) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def main() -> None:
    parser = argparse.ArgumentParser(description="mock OpenAI 兼容服务")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
