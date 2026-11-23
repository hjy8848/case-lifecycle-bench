# CaseLifecycleBench

CaseLifecycleBench 评测客服 Agent 能不能跨多次会话把一个问题处理到底。环境用虚拟时间推进支付、物流和跟进任务。Agent 每次唤醒都从新的上下文开始，通过 CRM 备注和跟进任务接着做。

当前可以运行四个场景：退款静默失败、写入超时但已提交、订单取消与仓储履约状态不一致、跨渠道回访。每个场景都有规则 Oracle 和故意出错的轨迹。评测分别记录对话成功、业务结果、阶段正确性和不安全操作。

## 本地运行

使用 Python 3.12。规则 Oracle 和评测测试只用标准库。

```powershell
python -m case_lifecycle_bench --case t1 --wake poll
python -m case_lifecycle_bench --case t2 --wake push
python -m case_lifecycle_bench --case t3 --wake poll --trace
python -m case_lifecycle_bench --case t9 --agent duplicate-ticket
python -m unittest discover -s tests -v
```

`--trace` 会在 JSON 结果中附上轨迹和状态。`--agent` 可以选择反例，例如 `--case t2 --agent blind-retry`。

在已安装 [τ-bench v1.0.1 固定版本](https://github.com/sierra-research/tau2-bench/tree/fc0055dc4e0a316c3f83133267fbd6faaa770992) 的 Python 环境里，可用 `--retail-order '#W6304490'` 为 T2 初始化公开 Retail 订单。跨会话的世界状态和退款写入由本项目的环境处理。

模型入口接受 `--model` 和可选的 `--user-model`，从进程环境读取 `CASE_LIFECYCLE_API_BASE` 和 `CASE_LIFECYCLE_API_KEY`。

用户模拟器可以使用独立接口。用 `CASE_LIFECYCLE_USER_API_BASE` 指定其接口，按需提供独立的 `CASE_LIFECYCLE_USER_API_KEY`。本地 NPU 服务可通过已建立的 SSH 本地转发访问，无须把客服网关凭据传给它。对支持该参数的部署，可用 `CASE_LIFECYCLE_USER_ENABLE_THINKING=false` 关闭用户模拟器的 thinking。

`--memory crm` 是默认设置。使用 `--memory full-history` 可以在唤醒时提供之前的可见对话和工具记录。T1、T2、T9 支持 `--memory ledger`，唤醒时提供结构化待办，并检查重复退款和关单条件。被拦截的动作在 `ledger_guard_events` 中单独记录。

## 生成 Pilot 用例

在已安装固定版本 τ-bench 的环境中运行下列命令。它从公开 Retail 数据抽取 30 笔不同订单，绑定到四个模板，然后用规则 Oracle 在两种唤醒模式下验收。

```powershell
python -m case_lifecycle_bench.pilot --seed 300 --output runs/pilot-seed300.json --trace
```

## 复现九用例记忆对照

先设置实验网关的进程环境，再从上述 Pilot 固定选取 T1、T2、T9 各三个用例。在 Poll-only 下，每个用例运行三种记忆设置。保留同一个输出目录再次运行时，批次会跳过已保存的轨迹。

```powershell
python -m case_lifecycle_bench.panel --pilot runs/pilot-seed300.json |
    Set-Content -Encoding utf8NoBOM runs/panel-seed300.json
python -m case_lifecycle_bench.panel_run --panel runs/panel-seed300.json --output runs/memory-panel --model qwen3.6-plus --workers 4
$traces = (Get-ChildItem -LiteralPath runs/memory-panel -Filter '*--*.json').FullName
python -m case_lifecycle_bench.compare --panel runs/panel-seed300.json $traces
```

## 首周正式冒烟

重新生成 Pilot，使用新规格下经 Oracle 验收的 30 个用例。每个用例用 CRM 在 Poll-only 和 Event-push 下各跑三次，客服模型为 `qwen3.6-plus`。下例指定通过三项最小行为预检的 NPU 用户模拟器 `qwen3.6-35b-a3b`。三个试次以 0–2 编号。实跑前需要设置用户模拟器的本地转发地址。

```powershell
python -m case_lifecycle_bench.pilot --seed 300 --output runs/pilot-duty-seed300.json
$userModel = 'qwen3.6-35b-a3b'
$env:CASE_LIFECYCLE_USER_ENABLE_THINKING = 'false'
python -m case_lifecycle_bench.panel_run --pilot runs/pilot-duty-seed300.json --output runs/official-smoke --model qwen3.6-plus --user-model $userModel --workers 2 --dry-run
python -m case_lifecycle_bench.panel_run --pilot runs/pilot-duty-seed300.json --output runs/official-smoke --model qwen3.6-plus --user-model $userModel --workers 2
python -m case_lifecycle_bench.smoke_summary runs/official-smoke
```

汇总支持 `--simulator-reviews` 接收另行审阅的标签。审阅者按 `case_id`、`wake`、`trial` 对应到具体轨迹，未审阅轨迹会保留待审状态。

用 `python -m case_lifecycle_bench.false_claim <轨迹文件>` 可对归档轨迹运行离线虚假陈述规则。传入 `--model` 和 `--model-version` 后会调用网关模型做语义审阅，输出声明原句、轨迹序号、证据和裁判版本。

在相同的 τ-bench v1.0.1 环境中运行原版 Retail 基线。下面沿用相同的两个角色模型，显式指定 base split、50 题、seed 300，并保留原版的 200 步上限。客服密钥从进程环境传入。用户模拟器接不要求认证的本地 NPU 转发，`local-no-auth` 是接口客户端的占位值。

```powershell
$env:OPENAI_API_KEY = $env:CASE_LIFECYCLE_API_KEY
try {
    $gatewayArgs = @{api_base = $env:CASE_LIFECYCLE_API_BASE; temperature = 0; num_retries = 0} | ConvertTo-Json -Compress
    $userArgs = @{api_base = $env:CASE_LIFECYCLE_USER_API_BASE; api_key = 'local-no-auth'; temperature = 0; num_retries = 0; extra_body = @{chat_template_kwargs = @{enable_thinking = $false}}} | ConvertTo-Json -Depth 5 -Compress
    python -m tau2.cli run --domain retail --task-split-name base --num-tasks 50 --num-trials 1 --seed 300 --max-steps 200 --max-concurrency 2 --max-retries 0 --agent-llm openai/qwen3.6-plus --agent-llm-args $gatewayArgs --user-llm "openai/$userModel" --user-llm-args $userArgs --save-to (Join-Path $PWD 'runs/tau-retail-50')
} finally {
    Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
}
```

## 项目状态

当前分工、验收证据和待办统一维护在 [工作进度](PROGRESS.md)。
