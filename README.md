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

`--memory crm` 是默认设置。使用 `--memory full-history` 可以在唤醒时提供之前的可见对话和工具记录。T1 还支持 `--memory ledger`，唤醒时提供结构化待办，并检查重复退款和关单条件。被拦截的动作在 `ledger_guard_events` 中单独记录。

## 生成 Pilot 用例

在已安装固定版本 τ-bench 的环境中运行下列命令。它从公开 Retail 数据抽取 30 笔不同订单，绑定到四个模板，然后用规则 Oracle 在两种唤醒模式下验收。

```powershell
python -m case_lifecycle_bench.pilot --seed 300 --output runs/pilot-seed300.json --trace
```

## 项目状态

当前分工、验收证据和待办统一维护在 [工作进度](PROGRESS.md)。
