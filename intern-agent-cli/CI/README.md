# Intern Agent CI

这个目录是 CI 的统一入口。case、action、assertion 都通过 registry 暴露给 CLI；不维护手写清单文档。

## 目录

```text
intern-cli/CI/
  run_ci.py              # CI 入口；运行、dry-run、registry 查询
  run_light_ci.py        # PR 前本地门禁
  runner/
    runner.py            # 本地阶段、部署、远端 case 调度 facade
    planner.py           # F/J resource-lock planner 与 conflict graph artifacts
    scheduler.py         # remote case wave/slot scheduling
    stage_0_preflight.py # F/J 跑前合同校验
    stage_1_unit_test.py # 单元测试/PR gate stage
    stage_2_package_deploy.py # package、payload、debug 部署与 harness sync stage
    stage_3_F.py        # F 功能测试执行 stage
    stage_4_J.py        # J 用户旅程测试执行 stage
  cases/selector.py      # --case / --case-list / --case-set 选择逻辑
  AUTHORING.md           # 新增 F/J case 的自服务指南
  cases/                 # F/J case 唯一权威定义
    F/F_00xx.py          # F 功能测试
    J/J_00xx.py          # J 用户旅程测试
  actions/registry.py    # action registry，包括 ctx action、stage action、GUI→CLI 映射
  assertions/registry.py  # assertion registry，包括 ctx assertion 和 native verifier
```

## 执行顺序

正式部署 CI 分两阶段：

1. Phase 1 部署门禁：打包当前分支，部署到两台新的测试机器，并验证部署成功。这个阶段不创建 `F_` 或 `J_` case。
2. Phase 2 用例测试：Phase 1 通过后，才运行 `F_` 功能测试和 `J_` 用户旅程测试。

## 查询 Registry

```bash
python3 intern-cli/CI/run_ci.py --list-cases
python3 intern-cli/CI/run_ci.py --list-case-sets
python3 intern-cli/CI/run_ci.py --list-actions
python3 intern-cli/CI/run_ci.py --list-assertions
python3 intern-cli/CI/run_ci.py --audit-registry --json
```

展开描述、参数、资源和注意事项：

```bash
python3 intern-cli/CI/run_ci.py --list-actions --details
python3 intern-cli/CI/run_ci.py --list-assertions --details
python3 intern-cli/CI/run_ci.py --list-cases --details
```

## 运行命令

PR 前本地门禁：

```bash
python3 intern-cli/CI/run_light_ci.py
```

dry-run 单 case：

```bash
python3 intern-cli/CI/run_ci.py --machines debug --case F_00xx_<slug> --dry-run --report /tmp/intern_agent_CI/F_00xx_dry/report.json
python3 intern-cli/CI/run_ci.py --machines debug --case J_00xx_<slug> --dry-run --report /tmp/intern_agent_CI/J_00xx_dry/report.json
```

实机单 case：

```bash
python3 intern-cli/CI/run_ci.py --machines debug --use-existing-deployment --case F_00xx_<slug> --report /tmp/intern_agent_CI/F_00xx_real/report.json
python3 intern-cli/CI/run_ci.py --machines debug --use-existing-deployment --case J_00xx_<slug> --report /tmp/intern_agent_CI/J_00xx_real/report.json
```

`--use-existing-deployment` 是 Phase 2 用例测试入口。它使用当前分支的 `intern-cli/CI` harness，但只同步 harness 到 debug 机器；不会执行 unit、package、Feishu cleanup、remote reset、deploy、repo cleanup 或 bootstrap，也不会重启 relay/daemon。产品 CLI/API 调用仍指向 Phase 1 已部署的 `extension/bundled-cli`。

选择 case set：

```bash
python3 intern-cli/CI/run_ci.py --machines debug --case-set F --dry-run
python3 intern-cli/CI/run_ci.py --machines debug --use-existing-deployment --case-set F --parallel-workers 4
```

## F 和 J

F 功能测试在 Phase 1 通过后运行，不出现飞书群。它验证 CLI、GUI、daemon、relay、VSIX 等部署后基础能力，用户看不到现场，因此只能通过 report 判断通过情况。GUI 按钮如果最终触发 CLI，对应 CLI 等价动作必须登记到 action registry，并用 F case 覆盖。

J 用户旅程测试在 Phase 1 通过后运行，关键操作都和真实飞书群相关。用户应能通过 GUI 和飞书群复现该流程。J case 完成后应留下飞书群现场和 `chat_id` 供检查；如果最后一步是删除 intern，则对应飞书群现场应不存在。

新增 F/J case 的文件路径分别是 `intern-cli/CI/cases/F/F_00xx.py` 和 `intern-cli/CI/cases/J/J_00xx.py`。CI registry 只从这两个 stage 目录发现 F/J case。

新增或修改 CI case 前读 `AUTHORING.md`。
