# 通知渠道

审核结果出来（PR 回帖成功）之后，把这条消息发到群里 / 内部系统。

```
backend/notifiers/
  wecom/                 企业微信群机器人
    notifier.yaml        清单：id、name、enabled、entry、options
    notifier.py          render(event, options) + send(event, options)
```

## 和 `plugins/` 的区别（别搞混）

| | `backend/plugins/` | `backend/notifiers/` |
|---|---|---|
| 一个目录 = | 一条业务线（专家） | 一个通知渠道 |
| 进不进库 | 投影成 `expert` / `agent` 两张表 | 不进库 |
| 前端专家中心 | 会多出一张卡片 | 不出现 |
| 清单文件 | `plugin.yaml` | `notifier.yaml` |
| 必填入口 | `entry.chat` / `entry.job` | `entry.send` |

把推送塞进 `plugins/` 的后果是专家中心多出一张「企业微信推送」专家卡片，
而 `plugin.yaml` 的 `stages`（侦察官→审核官→裁决官）对它毫无意义。所以分成两层。

## 加一个新渠道（钉钉 / 飞书 / 内部 webhook）

1. 复制 `wecom/` 改名为 `<你的 id>`。**目录名必须是小写字母 / 数字 / 下划线**（要当 Python 包名）。
2. 改 `notifier.yaml`：`id`、`name`、`icon`、`options`。
3. 重写 `notifier.py` 的两个函数：

```python
def available(options: dict) -> bool:
    """这个渠道现在能不能用（webhook 填了没）。返回 False 会被派发层跳过。"""
    return bool(options.get("webhook"))

async def send(event: dict, options: dict) -> dict:
    """发一条。**不要抛异常**，失败也返回描述。"""
    return {"ok": True, "ms": 12}
```

4. 重启服务。`/health` 的 `notifiers.channels` 里能看到它。

渲染逻辑（`render`）是可选的：如果渠道只是把 JSON POST 出去，就不需要渲染，
直接在 `send` 里把 `event` 序列化即可。

## 事件体长什么样

`backend/app/services/notify_event.py` 统一算好，渠道只负责渲染，
**不要自己去翻 review 记录、自己算 severity_counts**——那样加第二个渠道就会
出现「企微说 10 处、钉钉说 8 处」。

```json
{
  "kind": "review",
  "repo": "group/repo",
  "pr": {
    "number": 8, "title": "...", "author": "hedongyun",
    "url": "https://...", "repo": "group/repo",
    "headBranch": "dev", "baseBranch": "sit"
  },
  "risk": "high",                  // high / medium / low
  "decision": "request_changes",   // approve / comment / request_changes
  "issueCount": 10,
  "counts": {"blocker": 1, "major": 8, "minor": 1, "nit": 0},
  "blocking": 9,                   // blocker + major
  "mergeable": false,              // 布尔值，给只看字段的系统用
  "summary": "...",
  "feature": "这次改了什么",
  "stats": {"files": 26, "additions": 780, "deletions": 119},
  "issues": [{"severity": "blocker", "file": "a/B.java", "line": 111, "title": "..."}],
  "at": 1793950638548,
  "trigger": "auto"                // auto=自动回帖后, manual=页面点回帖
}
```

**硬规则在事件层已经执行过一次**：有 blocker/major 时 `decision` 一定是
`request_changes`，有 blocker 时 `risk` 一定是 `high`。渠道不需要（也不应该）
再判断一遍——复制的规则迟早会走样。

## 配置

`notifier.yaml` 的 `options` 原样传给 `send()`，由渠道自己解释：

```yaml
options:
  webhook: ""              # 仓库里一律留空，真实值走环境变量（见下）
  max_blocking: 5          # 消息里最多列几条必须处理的问题
```

### webhook 为什么留空

仓库是 **public**，而 webhook 的 key 等同于「往这个群发消息」的权限——
一旦提交上去，任何人都能给群推消息，而且即使之后改成 private，
git 历史里也已经删不干净了。

真实值放仓库根的 `.env`（已在 `.gitignore` 里）：

```bash
cp .env.example .env
# 编辑 .env，填入 WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

`backend/app/config.py` 启动时会读这个文件，**已存在的系统环境变量优先**，
所以 CI / 容器里照常注入 `WECOM_WEBHOOK` 即可，不会被 `.env` 覆盖。

优先级从高到低：

1. 系统环境变量 `WECOM_WEBHOOK`
2. 仓库根 `.env` 里的 `WECOM_WEBHOOK`
3. `notifier.yaml` 的 `options.webhook`（仓库里留空，本地一般不用改）

**敏感值在自检接口里会被遮掉中间部分**（`webhook` / `token` / `secret` /
`key` / `password`），`/health` 是给人看的，也可能被截图发群里。

## 开关

| 目的 | 做法 |
|---|---|
| 停用某个渠道 | `notifier.yaml` 里 `enabled: false` |
| 停用整个通知能力 | 环境变量 `NOTIFIERS_ENABLED=0` |
| 换群 / 换地址 | 改 `.env` 的 `WECOM_WEBHOOK` |

停用的渠道仍会出现在 `/health` 的 `notifiers.channels` 里，只是不再发消息——
比删目录好，改一个值就能恢复。

## 排查

```bash
curl -s localhost:8790/health | python -c "import json,sys; print(json.dumps(json.load(sys.stdin)['notifiers'], ensure_ascii=False, indent=1))"
```

会看到 `enabled`（总开关）、`count`（扫到几个）、`active`（几个会真发）、
每个渠道的 `available`，以及 `problems`（哪个目录写错了、错在哪）。
