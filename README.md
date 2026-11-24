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

## 自建 T3 的同题对照

下列命令使用自建电商订单，不依赖 τ-bench 数据。生命周期版保留跟进和等待。单会话版在拦截请求提交后让承运商结果立即生效，仍要求查证拦截和退款状态、通知客户。

```powershell
python -m case_lifecycle_bench --case t3 --paired --trace
python -m case_lifecycle_bench --case t3 --paired --agent premature-refund
```

两版使用相同的初始状态和业务结果判据。即时完成的核实计入履约，仍待办的跟进继续标为未履约。模型在单会话版收到当场核实的时间规则，多会话版保留原来的跟进期限。

## 模板生成与验收

每个模板先定义时间线、业务事件和客户回访条件。生成器用固定种子产出 10 个实例，改变事件时点、客户反应及合成标识。验收入口要求 Oracle 在 Poll-only 和 Event-push 下逐条通过所有适用检查，并要求 2–3 个反例在两种模式下触发各自的评分项。真实模型轨迹用于检查模型是否实际走到关键业务步骤，并供人工判断难度。

```powershell
python -m case_lifecycle_bench.template_check --template insurance-renewal --seed 300
```

`--output` 可保存生成实例和评分证据。用 `--smoke-trace <轨迹.json>` 复算真实模型试跑，轨迹需带用例哈希、模型 ID 和调用次数。

- [生命周期引擎](case_lifecycle_bench/lifecycle.py) 维护虚拟时钟、事件触发、唤醒、工具轨迹和 CRM 跟进。行业提供业务状态、工具以及允许的状态变化。
- [共同评分函数](case_lifecycle_bench/eval_checks.py) 使用同一条轨迹计算对话成功、事件查证延迟和条件回访等观测量。查证必须读到事件改变后的权威记录。结案和违规谓词由模板定义。
- 新模板在 [验收入口](case_lifecycle_bench/template_check.py) 登记生成器、Oracle、反例和必过的业务检查。

信贷逾期跟进、贷款审批补件和上门维修使用同一个状态推进引擎，并已按设计卡返工。三个模板建在 [servicing 引擎](case_lifecycle_bench/industries/servicing/core.py) 上，题目带 `terminal_variant`，评分见上面的三个结果。可用 `--instance-seed` 复现单道生成题。

```powershell
python -m case_lifecycle_bench.template_check --template credit-delinquency --seed 300
python -m case_lifecycle_bench.template_check --template credit-application --seed 300
python -m case_lifecycle_bench.template_check --template field-service-repair --seed 300
python -m case_lifecycle_bench --case credit-delinquency --instance-seed 301 --wake push --trace
```

## T3 随机订单号对照

用种子 1–30 各生成一对订单。每对共用客户号、退款金额和 T3 流程，订单号分别使用 `#W` 格式和 13 位纯数字。运行后按格式报告实际写入的拦截前退款、首次违规所在会话及 Wilson 95% 区间。先设置已有推理网关，再执行：

```powershell
python -m case_lifecycle_bench.t3_id_sweep --model qwen3.6-35b-a3b --seeds 30 --styles w7,numeric --wakes poll --output runs/t3-id-sweep --workers 4 --disable-thinking
```

四个本地转发地址可通过 `--api-bases` 一并传入。同配置重跑会跳过已有轨迹。省略 `--model` 时用 Oracle 检查流程，请使用单独的输出目录。

## 保险续保的模拟外呼

这道合成用例可离线运行。承保系统独立发布报价，接通外呼后先核验身份，再取得对当前报价的明确同意。续保确认与报价送达分别计分。

```powershell
python -m case_lifecycle_bench --case insurance-renewal --wake poll --trace
python -m case_lifecycle_bench --case insurance-renewal --wake push
python -m case_lifecycle_bench --case insurance-renewal --wake poll --quote-ready-at 'D2 14:00'
python -m case_lifecycle_bench --case insurance-renewal --wake push --quote-ready-at 'D2 14:00' --revise-quote-after-consent
python -m case_lifecycle_bench --case insurance-renewal --agent early-outreach
python -m case_lifecycle_bench --case insurance-renewal --agent missing-quote-consent
python -m case_lifecycle_bench --case insurance-renewal --agent missed-notification
```

报价发布时间可在 D1 10:00 之后至 D3 09:00 之间调整。晚于首次复查的报价会在下一次复查时被 Poll 查到。可用 `--model` 接已有推理网关来扮演客服。客户接听、身份核验和报价同意由合成环境返回。

## 通用 Ledger 五模板对照

通用 Ledger 从已观察到的工单、跟进任务及标有 `Commitment:` 或 `承诺：` 的工单备注生成会话记忆。对照的三个臂共用同一套工具、业务规则和记录承诺的提示。每个模板固定一个用例，在 Poll-only 下各运行一次 CRM、完整历史和通用 Ledger。原有零售业务规则的守卫仍可通过 `--memory ledger` 运行。

```powershell
python -m case_lifecycle_bench.ledger_panel --output runs/task-ledger-oracle
python -m case_lifecycle_bench.ledger_panel --output runs/task-ledger-panel --model qwen3.6-35b-a3b
```

有多个推理副本时，可用 `--api-bases` 传入地址。每个用例的三个设置保留在同一副本运行。单独跑一个用例时可选 `--memory task-ledger`。

## 三个结果与终态变体

未结案不等于失败。带 `terminal_variant` 的用例把评分拆成三个结果：对话成功由客户决定，`case_resolution` 表示责任履行，业务结果另存为 `business_outcome`，只用于分组。责任履行只看 Agent 做了什么：终态与同一实例下 Oracle 的终态一致、全部行为类过程检查通过（工具调用、时间、次数、顺序、系统状态）、没有被工具拦截的不安全动作。`wrongful_closure` 标记业务没有解决、Agent 却关了单。内容类检查，包括转述的原因、确认时段的内容、“已收到／已入账”这类说法和承诺结果，照常计算，写进评估结果的 `content` 字段，不计入主分数，以后接裁判时直接重算。混合检查只保留行为部分，例如关单前通知客户只要求在正确时间发出了消息。没有 `terminal_variant` 的用例保持原来的业务结果判据，等对应模板返工后再切换。

三种变体由同一粒种子产生，只差关键事件的时间。已解决：结果在截止前生效，Agent 核实、通知、关单。继续等：仍在处理且没超时限，Agent 保持工单打开、按时复查、如实告知并排好下一次跟进。该升级：计划失败或超过时限，Agent 转人工并告知客户。连续 10 个种子按 5／3／2 分配。T3 是第一个例子，继续等对应承运商结果在截止之后才出现。

```powershell
python -m case_lifecycle_bench.variant_check --seed 300
```

验收脚本检查 Oracle 在两种唤醒下对每个实例都履行责任，什么都不做、提前结案和拦截前退款的反例在所有实例上都不能通过，并按变体分别报告责任履行率和错误结案率。

## 用真实模型跑卡片类模板

信贷 L1、上门 H1 和信贷 L2 可用模型批跑，客服 Agent 和客户模拟器是同一个网关上的聊天模型。默认客户在聊天回访里由模拟器扮演，外呼的客户按实例里的脚本回答。加 `--llm-customer` 后外呼客户也由模拟器扮演：工具结果不再带客户的私有意图（承诺日期、选定的时段），Agent 只能从客户的话里听到；人设来自实例里的 `persona`，参照用的 Oracle 仍按脚本模式现跑。

```powershell
python -m case_lifecycle_bench.template_run --template field-service-repair `
    --model qwen3.6-35b-a3b --api-bases http://127.0.0.1:18101/v1 http://127.0.0.1:18102/v1 `
    --seeds 10 --output runs/template-run --disable-thinking --llm-customer
```

每个实例存一份带轨迹的 JSON，已存在的结果会复用，结尾按变体汇总责任履行率、不安全次数和错误结案。脚本用完后客户按实例的默认设定继续接听，一次浪费的外呼不会让用例无解。承诺日期的写法不限，按时刻比较。

### 在服务器上部署和批跑

NPU 机器上的 vLLM 副本只监听 127.0.0.1，所以直接把项目放到那台机器上跑，请求走本机，不用 SSH 转发，转发掉线也就不会丢任务。项目只依赖标准库，用机器上已有的 Python 3.12 即可。

```powershell
# 本机：按提交打包，传到服务器上的新目录
git archive --format=tar --prefix=CaseLifecycleBench-<提交>/ -o clb.tar HEAD
scp clb.tar npu30001:/home/<用户>/projects/ ; ssh npu30001 'cd projects && tar -xf clb.tar && rm clb.tar'
```

```bash
# 服务器：测试、启动、查看
cd ~/projects/CaseLifecycleBench-<提交>
PYTHON=/path/to/python3.12 bash -c '$PYTHON -m unittest discover -s tests'
PYTHON=/path/to/python3.12 WORKERS=1 SEEDS=10 bash deploy/start_in_tmux.sh
tail -f runs/server-*/progress.log
python -m case_lifecycle_bench.template_summary runs/server-*/l1-llm
```

`deploy/run_templates.sh` 同时跑五个任务：两个外呼模板各用脚本客户和 LLM 客户跑一次，贷款模板跑一次。每个任务对输出目录做第二遍，只补跑失败的。`WORKERS` 是每个任务在每个副本上同时跑的实例数，五个任务叠加后每个副本同时有五倍的请求。`template_summary` 用当前评分器从轨迹重算汇总，所以评分修正会作用到旧结果。

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
