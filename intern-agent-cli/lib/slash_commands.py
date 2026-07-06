"""Shared slash-command metadata for Feishu daemon/relay surfaces."""

from typing import Iterable, Tuple


CommandRows = Iterable[Tuple[str, str]]

RELAY_HELPER_COMMAND = "/helper"
RELAY_MACHINE_CONFIG_COMMAND = "/machine_config"
RELAY_TRIGGER_MODE_COMMAND = "/trigger_mode"
RELAY_DETAIL_MODE_COMMAND = "/detail_mode"
RELAY_CONFIG_COMMAND = "/config"
RELAY_UPGRADE_COMMAND = "/upgrade"

NATIVE_SLASH_COMMANDS_BY_INTERN_TYPE = {
    "claude": {
        "/clear": "清空对话历史",
        "/stop": "停止当前执行",
        "/compact": "压缩上下文",
        "/help": "显示 Claude Code 帮助",
        "/cost": "显示 Claude Code token/cost 信息",
        "/model": "切换或查看 Claude Code 模型",
        "/screenshot": "返回当前 tmux 屏幕图片快照",
        "/btw": "旁路提问（Claude Code 原生，答案回传飞书；用法：/btw <问题>）",
    },
    "codex": {
        "/clear": "清空对话历史",
        "/compact": "压缩上下文（Codex 原生 slash command；不触发 PreCompact hook）",
        "/help": "显示 Codex 帮助",
        "/cost": "显示 Codex token/cost 信息",
        "/model": "切换或查看 Codex 模型",
        "/goal": "设置/管理 Codex 原生 goal（用法：/goal <目标>、/goal clear、/goal status、/goal resume）",
        "/stop": "停止当前执行（发送 Escape 键）",
        "/screenshot": "返回当前 tmux 屏幕图片快照",
    },
}

RELAY_GROUP_COMMANDS = (
    (
        RELAY_MACHINE_CONFIG_COMMAND,
        "配置当前机器级能力（intern 群或 helper 群；relay 截获）",
    ),
    (
        RELAY_TRIGGER_MODE_COMMAND,
        "查看/设置群触发模式（用法：/trigger_mode all | at_only；relay 截获）",
    ),
    (
        RELAY_DETAIL_MODE_COMMAND,
        "查看/设置处理中消息明细级别（用法：/detail_mode full | summary；relay 截获）",
    ),
    (
        RELAY_CONFIG_COMMAND,
        "打开群配置卡片（relay 截获）",
    ),
    (
        RELAY_UPGRADE_COMMAND,
        "检查并升级当前机器客户端（relay 截获）",
    ),
    (
        RELAY_HELPER_COMMAND,
        "helper/主入口机器辅助命令（用法：/helper status|start|stop|invite-owner|migrate ...）",
    ),
)


def format_command_section(title: str, commands: CommandRows) -> str:
    rows = list(commands)
    if not rows:
        return ""
    return "\n".join([f"{title}:"] + [f"  {cmd} — {desc}" for cmd, desc in rows])


def format_available_slash_commands(intern_type: str, intern_commands: CommandRows) -> str:
    sections = []
    intern_section = format_command_section(
        f"intern 原生命令（{intern_type}）",
        intern_commands,
    )
    if intern_section:
        sections.append(intern_section)
    sections.append(format_command_section(
        "群级/机器级命令（relay 截获，不进入 intern TUI）",
        RELAY_GROUP_COMMANDS,
    ))
    return "\n\n".join(section for section in sections if section)
