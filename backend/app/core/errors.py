# -*- coding: utf-8 -*-
"""模型调用异常 → 人话

各家中转站/本地模型的报错格式五花八门，直接透传用户看不懂。
这里按 HTTP 状态与常见异常名归类，给出「哪个环节坏了 + 怎么改」。
"""


def fmt_exc(e: BaseException) -> str:
    msg = str(e).strip() or e.__class__.__name__
    low = msg.lower()
    cls = e.__class__.__name__.lower()
    if "401" in msg or "invalid api key" in low or "authentication" in low:
        return "模型 API Key 无效或没有权限（HTTP 401）。请检查左侧配置里的 Key。"
    if "403" in msg:
        return "没有访问该模型的权限（HTTP 403）。请检查 Key 权限或模型名。"
    if "404" in msg:
        return "接口或模型不存在（HTTP 404）。请检查 API 地址是否以 /v1 结尾、模型名是否正确。"
    if "429" in msg:
        return "请求被限流或额度不足（HTTP 429）。请稍后重试或检查账户额度。"
    if "econnrefused" in low or "connection error" in low or "connectionerror" in cls:
        return "连接模型接口失败（连接被拒绝/网络错误）：本地模型（如 Ollama）是否已启动？地址端口是否正确？"
    if "enotfound" in low or "getaddrinfo" in low:
        return "域名解析失败（ENOTFOUND）：请检查 API 地址拼写。"
    if "timeout" in low or "timed out" in low or "timeouterror" in cls:
        return "连接模型接口超时：请检查网络，或稍后重试。"
    if "error code: 5" in low or "badgateway" in low:
        return "模型接口返回 5xx 服务端错误，可能是中转站波动，请稍后重试。原始信息：" + msg[:200]
    return "智能体运行失败（%s）：%s" % (e.__class__.__name__, msg[:300])
