# CI 用例编写指南

本文给需要自己补 CI 的 intern 使用。目标是：先用 CLI 查清现有 case/action/assertion，再新增缺失的 action、assertion 和 case，最后用 dry-run 与单 case 实机命令拿到报告。

## 1. 测试类型

正式远端 CI 先跑 Phase 1 部署门禁：打包当前分支，部署到两台新的测试机器，并验证部署成功。这个阶段不写 case、不占用编号。Phase 1 通过后，才进入 F/J 用例测试。

| 类型 | 位置 | 判断标准 |
| --- | --- | --- |
| 单元/本地测试 | 普通 pytest/Jest 或本地 fixture | 不需要部署 debug 机器、真实 daemon、真实 relay、真实飞书群即可证明行为 |
| F 功能测试 | `intern-cli/CI/cases/F/F_00xx.py` | Phase 1 通过后运行；不出现飞书群，验证部署后 CLI/GUI/daemon/relay/VSIX 基础能力；结果只通过 report 判定 |
| J 用户旅程测试 | `intern-cli/CI/cases/J/J_00xx.py` | Phase 1 通过后运行；所有关键操作都和真实飞书群相关，用户可通过 GUI 和飞书群复现；结果必须留下飞书现场，若最后一步是删除 intern，则对应飞书群应不存在 |

F/J stage case 统一使用 `intern-cli/CI/cases/F` 或 `intern-cli/CI/cases/J`，CI registry 只从这两个 stage 目录发现用例。

F 可以是长流程，例如添加 workspace、切换 mode、创建 intern、删除 workspace。F 的重点是证明用户看不到现场时 report 仍能明确判定通过或失败。

J 是用户可见流程，例如创建 intern、分配 task、多轮 prompt、卡片点击、审批 merge、删除 intern。J 必须声明飞书群、intern、task、用户交互四类 action。

## 2. 查看现有测试、Action、Assertion

所有查询都走 registry，不维护手写 Markdown 清单。

```bash
python3 intern-cli/CI/run_ci.py --list-cases
python3 intern-cli/CI/run_ci.py --list-case-sets
python3 intern-cli/CI/run_ci.py --list-actions
python3 intern-cli/CI/run_ci.py --list-assertions
python3 intern-cli/CI/run_ci.py --audit-registry --json
```

需要看描述、参数、返回值、资源和注意事项时，加 `--details`：

```bash
python3 intern-cli/CI/run_ci.py --list-actions --details
python3 intern-cli/CI/run_ci.py --list-assertions --details
python3 intern-cli/CI/run_ci.py --list-cases --details
```

如果你需要的动作或判定已经在 registry 中，直接复用；如果没有，先补 action/assertion registry 和实现，再写 case。

## 3. 资源命名

新增 F/J case 的所有 case-scoped runtime/resource 名称必须带 stage+case 前缀，避免 F 和 J 并行时互相污染。F 使用 `ci_f_00xx`，J 使用 `ci_j_00xx`；具体资源从同一个 namespace 派生，例如：

- workspace/project/repo fixture: `ci_f_0025_workspace_{run_id}`、`ci_j_0001_workspace_{run_id}`
- intern: `intern_ci_f_0025_worker_{run_id}`、`intern_ci_j_0001_worker_{run_id}`
- task/file: `task_ci_f_0025_open_{run_id}`、`file_ci_j_0001_result.txt`
- Feishu group/chat/open_id/message、tmux/session、artifact/runtime 目录和 daemon/relay registry lookup prefix 也必须包含同一 stage-aware namespace

不要在 F/J 新 case 中新增 `ci_00xx`、`intern_ci_00xx`、`task_ci_00xx`、`ci_f00xx` 或 `intern_ci_j00xx` 这类不可区分或缺少分隔符的前缀。

## 4. 以 J 为例构造测试

先用上一节的 registry 查询确认是否已有可复用动作。如果缺少动作，在对应实现处补代码：

- 可复用 Python action：`intern-cli/CI/actions/`
- GUI 按钮动作：在 `intern-cli/CI/actions/registry.py` 登记 `gui_command` 和 `cli_equivalent`，并用 F case 覆盖 CLI 等价路径
- 远端真实流程动作：放入 `intern-cli/CI/cases/F/` 或 `intern-cli/CI/cases/J/`，由对应 case/domain 模块调度；不要新增 `native_remote.py` 或 `remote_cases/` 兼容路径

每个新增 action 都必须登记到 `intern-cli/CI/actions/registry.py`，写清：

- `id`
- `category`：`capability`、`intern`、`task`、`feishu_group`、`user_interaction` 或 `local_fixture`
- `description`
- `parameters`
- `resources`
- `notes`

如果缺少判定，在 `intern-cli/CI/assertions/` 或对应 verifier 中补实现，并登记到 `intern-cli/CI/assertions/registry.py`。Assertion 只检查事实，不做业务动作，不伪造最终状态。

然后创建 J case，例如：

```python
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="J_0001_single_intern_task_merge",
    name="Single intern task merge journey",
    description="用户在飞书群中驱动 intern 完成 task、打开 PR 并审批 merge。",
    kind="user_journey",
    tags=("J", "feishu", "task", "merge"),
    timeout_seconds=3600,
    extra={
        "ci_stage": "J",
        "actions": (
            "create_feishu_group",
            "create_intern",
            "create_task",
            "send_user_message",
            "click_feishu_card",
            "approve_merge",
        ),
        "resources": ("feishu_app", "feishu_group", "debug_machine", "daemon", "codeup", "llm"),
        "run_mode": "full_deploy",
        "journey_steps": (
            {
                "id": "s01_create_intern",
                "action": "create_intern",
                "input": {"name": "intern_ci_j0001_worker", "role": "independent"},
                "expect": {"intern_status": "Idle", "feishu_group": "created"},
            },
            {
                "id": "s02_assign_task",
                "action": "create_task",
                "input": {"prompt": "创建分支，修改目标文件，打开 PR，完成后等待审批。"},
                "expect": {"task_status": "Working", "pr_status": "OPEN"},
            },
            {
                "id": "s03_approve",
                "action": "click_feishu_card",
                "input": {"button": "approve_merge"},
                "expect": {"pr_status": "MERGED", "task_status": "Completed"},
            },
        ),
        "assertions": (
            "native.no_merge_before_approval",
            "native.master_file_content",
            "native.metadata_completed",
        ),
        "notes": (
            "case 结束时保留飞书群现场，report 写出 chat_id。",
        ),
    },
)
```

dry-run 验证：

```bash
python3 intern-cli/CI/run_ci.py --machines debug --case J_0001_single_intern_task_merge --dry-run --report /tmp/intern_agent_CI/J_0001_dry/report.json
```

dry-run 通过的标准是：case 能被选中，F/J preflight 通过，report 中没有 failed step。dry-run 不证明产品行为正确，只证明 registry、选择、资源声明和报告结构可执行。

## 5. 单 Case 实机测试

写完 action、assertion 和 case 后，intern 只需要跑对应单 case 命令并保存 report。
单 case 实机测试以 Phase 1 部署门禁通过为前提。Phase 2 必须使用 `--use-existing-deployment`：它只把当前分支的 `intern-cli/CI` harness 同步到 debug 机器，并用这个 harness 运行 case；产品 CLI/API 仍指向已部署的 `extension/bundled-cli`。这个模式不会执行 unit、package、Feishu cleanup、remote reset、deploy、repo cleanup 或 bootstrap，也不会重启 relay/daemon。

跨进程 debug resource lease 完成前，真实 debug 机器运行由 CI lead 排队确认，避免多个 intern 同时覆盖同一套远端 runtime。

F case：

```bash
python3 intern-cli/CI/run_ci.py --machines debug --use-existing-deployment --case F_00xx_<slug> --report /tmp/intern_agent_CI/F_00xx_real/report.json
```

J case：

```bash
python3 intern-cli/CI/run_ci.py --machines debug --use-existing-deployment --case J_00xx_<slug> --report /tmp/intern_agent_CI/J_00xx_real/report.json
```

运行结束后看 report：

```bash
python3 -m json.tool /tmp/intern_agent_CI/J_00xx_real/report.json | sed -n '1,220p'
```

提交 PR 时提供：

- case id、stage、资源类型、run mode
- dry-run report 路径
- 单 case 实机 report 路径
- 运行命令和结果
- 失败分类：`CI logic error`、`Product bug`、`Environment/external` 或 `Unknown`
- 若是 J，提供保留的飞书群 `chat_id`；若最后一步删除 intern，则说明对应飞书群已不存在

CI 失败时先分类。若是产品 bug，CI case PR 不夹带产品修复，只记录复现证据。

## 6. 代码边界

F/J case 实现 PR 只修改 CI harness、case、registry、CI 文档和 CI 自身测试，例如 `intern-cli/CI/**`、`intern-cli/CI/tests/test_ci_*.py` 和任务 metadata。不要在同一个 PR 修改产品代码、VS Code extension 代码或产品测试文件。若 case 发现产品 bug，只在 report/history/knowledge 中记录复现证据；产品修复和产品测试由独立 bugfix 任务处理。
