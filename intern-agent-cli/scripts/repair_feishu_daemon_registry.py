#!/usr/bin/env python3
"""Repair local daemon Feishu chat registry after groups were dissolved.

This script intentionally does not touch relay state. Run it on an affected
user machine to repair the daemon-local outbound mapping:

  cd /work-agents
  python3 repair_feishu_daemon_registry.py

It checks local `.feishu_registry/*.json` chat mappings against the allowlist
and, by default, only repairs interns that are active on this machine. If an
allowlisted green-light old chat_id is no longer visible to the Feishu app, it
creates or uses a replacement group, updates the local registry file atomically,
and restarts the local daemon. It never restarts individual intern sessions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.enterprise_paths import daemon_owner_path, daemon_policy_path
from lib.log_paths import system_log_dir
from lib.tmux_session import scoped_tmux_session_name


BASE_URL = "https://open.feishu.cn/open-apis"
TYPE_EMOJI = {"claude": "🤖 ", "codex": "🚀 ", "copilot": ""}
WORK_AGENTS_ROOT = Path("/work-agents")
DEFAULT_INCIDENT_REPORT_NAME = "builtin:20260531-formal-affected-groups"
DEFAULT_INCIDENT_REPORT: dict[str, Any] = {
    "items": [
        {
            "old_chat_id": "oc_76fa9d15295b713704c712d0335a30cb",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_jing_addata;jing_agdata",
            "registry_name_candidates": "",
            "project_candidates": "",
            "type_candidates": "",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": ""
        },
        {
            "old_chat_id": "oc_cbb1b369870afcd7b20cee20767b6a92",
            "status": "present",
            "daemon_names": "intern_rule_test",
            "registry_name_candidates": "",
            "project_candidates": "",
            "type_candidates": "",
            "last_group_name_candidates": "",
            "name": "🔴 intern_rule_test/llm_intern_agents",
            "relay_keys": ""
        },
        {
            "old_chat_id": "oc_ca2fcb76de593892638594fa0e18d94e",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_test",
            "registry_name_candidates": "",
            "project_candidates": "",
            "type_candidates": "",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": ""
        },
        {
            "old_chat_id": "oc_b869a5c49421acea2d6e40e48f2e72a7",
            "status": "not_in_current_app_list",
            "daemon_names": "rule_bob",
            "registry_name_candidates": "",
            "project_candidates": "",
            "type_candidates": "",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": ""
        },
        {
            "old_chat_id": "oc_198ef19841fdb6a3861eb59a6725e957",
            "status": "not_in_current_app_list",
            "daemon_names": "test_feishu",
            "registry_name_candidates": "",
            "project_candidates": "",
            "type_candidates": "",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": ""
        },
        {
            "old_chat_id": "oc_af70e74b21ce98ca2d29ffbc2a7824d4",
            "status": "not_in_current_app_list",
            "daemon_names": "agcode_jing;intern_agcode_jing",
            "registry_name_candidates": "intern_agcode_jing",
            "project_candidates": "AutoDataOrch",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 agcode_jing/AutoDataOrch",
            "name": "🟢 🤖 agcode_jing/AutoDataOrch",
            "relay_keys": "AutoDataOrch:intern_agcode_jing"
        },
        {
            "old_chat_id": "oc_5d1239fdf783a73b1518ee2131426a71",
            "status": "not_in_current_app_list",
            "daemon_names": "agother_jing",
            "registry_name_candidates": "intern_agother_jing",
            "project_candidates": "AutoDataOrch",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 agother_jing/AutoDataOrch",
            "name": "🟢 🚀 agother_jing/AutoDataOrch",
            "relay_keys": "AutoDataOrch:intern_agother_jing"
        },
        {
            "old_chat_id": "oc_da483c219a32b9526c39d5c146dbf504",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_agsyn_jing",
            "registry_name_candidates": "intern_agsyn_jing",
            "project_candidates": "AutoDataOrch",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 agsyn_jing/AutoDataOrch",
            "name": "🟢 🚀 agsyn_jing/AutoDataOrch",
            "relay_keys": "AutoDataOrch:intern_agsyn_jing"
        },
        {
            "old_chat_id": "oc_25d8bbb4e1cef1e04a3517ca057dcd4b",
            "status": "not_in_current_app_list",
            "daemon_names": "agtest_jing",
            "registry_name_candidates": "intern_agtest_jing",
            "project_candidates": "AutoDataOrch",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 agtest_jing/AutoDataOrch",
            "name": "🟢 🤖 agtest_jing/AutoDataOrch",
            "relay_keys": "AutoDataOrch:intern_agtest_jing"
        },
        {
            "old_chat_id": "oc_5d1442bf48579d1c993f8fc53c742c29",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_jing_agdata",
            "registry_name_candidates": "intern_jing_agdata",
            "project_candidates": "AutoDataOrch",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 jing_agdata/AutoDataOrch",
            "name": "🟢 🤖 jing_agdata/AutoDataOrch",
            "relay_keys": "AutoDataOrch:intern_jing_agdata"
        },
        {
            "old_chat_id": "oc_f0b3ae3a55cfd7a4d53527e9f1d42916",
            "status": "not_in_current_app_list",
            "daemon_names": "run;intern_run",
            "registry_name_candidates": "intern_run",
            "project_candidates": "AxisDataEngine",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 run/AxisDataEngine",
            "name": "🟢 🤖 run/AxisDataEngine",
            "relay_keys": "AxisDataEngine:intern_run"
        },
        {
            "old_chat_id": "oc_7231e8a4dedc1e4ab0c423ebab82c715",
            "status": "not_in_current_app_list",
            "daemon_names": "aworld_claude_jing",
            "registry_name_candidates": "intern_aworld_claude_jing",
            "project_candidates": "AxisSynData",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 aworld_claude_jing/AxisSynData",
            "name": "🟢 🤖 aworld_claude_jing/AxisSynData",
            "relay_keys": "AxisSynData:intern_aworld_claude_jing"
        },
        {
            "old_chat_id": "oc_5a5a89b29ed3c7290ad7dbc219027d83",
            "status": "not_in_current_app_list",
            "daemon_names": "cleanpr",
            "registry_name_candidates": "intern_cleanpr",
            "project_candidates": "Diloco",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "Diloco:intern_cleanpr"
        },
        {
            "old_chat_id": "oc_1a33977b1bc5c18b5f38004e5a274b28",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_fifa_features",
            "registry_name_candidates": "intern_fifa_features",
            "project_candidates": "FIFA2026",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 fifa_features/FIFA2026",
            "name": "🟢 🚀 fifa_features/FIFA2026",
            "relay_keys": "FIFA2026:intern_fifa_features"
        },
        {
            "old_chat_id": "oc_0689adeae13df992c40f35be929a3467",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_fifa_opt;fifa_opt",
            "registry_name_candidates": "intern_fifa_opt",
            "project_candidates": "FIFA2026",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 fifa_opt/FIFA2026",
            "name": "🟢 🚀 fifa_opt/FIFA2026",
            "relay_keys": "FIFA2026:intern_fifa_opt"
        },
        {
            "old_chat_id": "oc_1c311b2ac732a868b5ac54a95c120606",
            "status": "not_in_current_app_list",
            "daemon_names": "fix_r3_050901",
            "registry_name_candidates": "intern_fix_r3_050901",
            "project_candidates": "Itp-verl",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "Itp-verl:intern_fix_r3_050901"
        },
        {
            "old_chat_id": "oc_f05aa1c6602398c2f11d95fb528cc287",
            "status": "not_in_current_app_list",
            "daemon_names": "fixr3_test050920",
            "registry_name_candidates": "intern_fixr3_test050920",
            "project_candidates": "Itp-verl",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 fixr3_test050920/Itp-verl",
            "name": "🟢 🤖 fixr3_test050920/Itp-verl",
            "relay_keys": "Itp-verl:intern_fixr3_test050920"
        },
        {
            "old_chat_id": "oc_a794f3bbe0cb7222839eb8ee8c0607d3",
            "status": "not_in_current_app_list",
            "daemon_names": "fixr3_v2",
            "registry_name_candidates": "intern_fixr3_v2",
            "project_candidates": "Itp-verl",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "Itp-verl:intern_fixr3_v2"
        },
        {
            "old_chat_id": "oc_02c8decfb20e6e5dd432f30a05576fad",
            "status": "not_in_current_app_list",
            "daemon_names": "test050803",
            "registry_name_candidates": "intern_test050803",
            "project_candidates": "Itp-verl",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "Itp-verl:intern_test050803"
        },
        {
            "old_chat_id": "oc_f2d5e738b6769af373d3fca350de1455",
            "status": "not_in_current_app_list",
            "daemon_names": "test050901",
            "registry_name_candidates": "intern_test050901",
            "project_candidates": "Itp-verl",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "Itp-verl:intern_test050901"
        },
        {
            "old_chat_id": "oc_8c28131962aef4db0818ec9cdd772e67",
            "status": "not_in_current_app_list",
            "daemon_names": "verl0509",
            "registry_name_candidates": "intern_verl0509",
            "project_candidates": "Itp-verl",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "Itp-verl:intern_verl0509"
        },
        {
            "old_chat_id": "oc_9176b9ee86e1ae7b483ee0153870d74f",
            "status": "not_in_current_app_list",
            "daemon_names": "for_intern;intern_for_intern",
            "registry_name_candidates": "intern_for_intern",
            "project_candidates": "LLaVA-OneVision-1.5",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 for_intern/LLaVA-OneVision-1.5",
            "name": "🔴 🤖 for_intern/LLaVA-OneVision-1.5",
            "relay_keys": "LLaVA-OneVision-1.5:intern_for_intern"
        },
        {
            "old_chat_id": "oc_87c6a23353a82dac7766de6feb0e5662",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_llava_bugfix",
            "registry_name_candidates": "intern_llava_bugfix",
            "project_candidates": "LLaVA-OneVision-1.5",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 llava_bugfix/LLaVA-OneVision-1.5",
            "name": "🔴 🤖 llava_bugfix/LLaVA-OneVision-1.5",
            "relay_keys": "LLaVA-OneVision-1.5:intern_llava_bugfix"
        },
        {
            "old_chat_id": "oc_fd10cea1428ecfeb08ade342cc9e3959",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_llava_runner2;llava_runner2",
            "registry_name_candidates": "intern_llava_runner2",
            "project_candidates": "LLaVA-OneVision-1.5",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 llava_runner2/LLaVA-OneVision-1.5",
            "name": "🔴 🤖 llava_runner2/LLaVA-OneVision-1.5",
            "relay_keys": "LLaVA-OneVision-1.5:intern_llava_runner2"
        },
        {
            "old_chat_id": "oc_5835059e59e6e3364b01ac931d73f3f8",
            "status": "not_in_current_app_list",
            "daemon_names": "llavaov_runner;intern_llavaov_runner",
            "registry_name_candidates": "intern_llavaov_runner",
            "project_candidates": "LLaVA-OneVision-1.5",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 llavaov_runner/LLaVA-OneVision-1.5",
            "name": "🔴 🤖 llavaov_runner/LLaVA-OneVision-1.5",
            "relay_keys": "LLaVA-OneVision-1.5:intern_llavaov_runner"
        },
        {
            "old_chat_id": "oc_093ff49f4b80c7219fcbeebede1552df",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_ltp_script_exec;ltp_script_exec",
            "registry_name_candidates": "intern_ltp_script_exec",
            "project_candidates": "LTP_scripts",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 ltp_script_exec/LTP_scripts",
            "name": "🔴 🤖 ltp_script_exec/LTP_scripts",
            "relay_keys": "LTP_scripts:intern_ltp_script_exec"
        },
        {
            "old_chat_id": "oc_56fa1fd25134f9b0cb47980e26a0dbed",
            "status": "not_in_current_app_list",
            "daemon_names": "jing_meddata;intern_jing_meddata",
            "registry_name_candidates": "intern_jing_meddata",
            "project_candidates": "MedUniA",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "MedUniA:intern_jing_meddata"
        },
        {
            "old_chat_id": "oc_f60e1e6294b8696e9cb21658abd73e22",
            "status": "not_in_current_app_list",
            "daemon_names": "jing_medunia",
            "registry_name_candidates": "intern_jing_medunia",
            "project_candidates": "MedUniA",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 jing_medunia/MedUniA",
            "name": "🟢 🤖 jing_medunia/MedUniA",
            "relay_keys": "MedUniA:intern_jing_medunia"
        },
        {
            "old_chat_id": "oc_c0fb8e9a99b9cbe9b5b12e0310907960",
            "status": "not_in_current_app_list",
            "daemon_names": "yixuan_wang_deepep_startup",
            "registry_name_candidates": "intern_yixuan_wang_deepep_startup",
            "project_candidates": "Megatron-LM",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "Megatron-LM:intern_yixuan_wang_deepep_startup"
        },
        {
            "old_chat_id": "oc_39b7a9496cb21c12531d23f868370f37",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_memvla_developer;memvla_developer",
            "registry_name_candidates": "intern_memvla_developer",
            "project_candidates": "MemoryVLA",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "MemoryVLA:intern_memvla_developer"
        },
        {
            "old_chat_id": "oc_1533b28e41a1e781a0017f1c81393c10",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_local_verfier",
            "registry_name_candidates": "intern_local_verfier",
            "project_candidates": "MiroFlow_modify",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "MiroFlow_modify:intern_local_verfier"
        },
        {
            "old_chat_id": "oc_d9f0f3bcc21e314568c67cc71e29b0de",
            "status": "not_in_current_app_list",
            "daemon_names": "miroflow_test;intern_miroflow_test",
            "registry_name_candidates": "intern_miroflow_test",
            "project_candidates": "MiroFlow_modify",
            "type_candidates": "codex",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "MiroFlow_modify:intern_miroflow_test"
        },
        {
            "old_chat_id": "oc_3b536acabc9927dbd4ea32097087d237",
            "status": "not_in_current_app_list",
            "daemon_names": "ourmodel_test",
            "registry_name_candidates": "intern_ourmodel_test",
            "project_candidates": "MiroFlow_modify",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 ourmodel_test/MiroFlow_modify",
            "name": "🔴 🚀 ourmodel_test/MiroFlow_modify",
            "relay_keys": "MiroFlow_modify:intern_ourmodel_test"
        },
        {
            "old_chat_id": "oc_3369949bc264585c49d911b04cb4525d",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_summary_tool",
            "registry_name_candidates": "intern_summary_tool",
            "project_candidates": "MiroFlow_modify",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 summary_tool/MiroFlow_modify",
            "name": "🔴 🚀 summary_tool/MiroFlow_modify",
            "relay_keys": "MiroFlow_modify:intern_summary_tool"
        },
        {
            "old_chat_id": "oc_6000505a6ed48758598c30f96ad56193",
            "status": "not_in_current_app_list",
            "daemon_names": "nemontron_review_cc",
            "registry_name_candidates": "intern_nemontron_review_cc",
            "project_candidates": "Nemotron",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 nemontron_review_cc/Nemotron",
            "name": "🟢 🤖 nemontron_review_cc/Nemotron",
            "relay_keys": "Nemotron:intern_nemontron_review_cc"
        },
        {
            "old_chat_id": "oc_303d47e28776ec0cb9de0f3c8ec6ccd1",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_jingjing_neuroscaler;jingjing_neuroscaler",
            "registry_name_candidates": "intern_jingjing_neuroscaler",
            "project_candidates": "NeuroScaler",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 jingjing_neuroscaler/NeuroScaler",
            "name": "🔴 🚀 jingjing_neuroscaler/NeuroScaler",
            "relay_keys": "NeuroScaler:intern_jingjing_neuroscaler"
        },
        {
            "old_chat_id": "oc_ebaaccd366382334b8ea89c6443945ad",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_neuro_test;neuro_test",
            "registry_name_candidates": "intern_neuro_test",
            "project_candidates": "NeuroScaler",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 neuro_test/NeuroScaler",
            "name": "🟢 🚀 neuro_test/NeuroScaler",
            "relay_keys": "NeuroScaler:intern_neuro_test"
        },
        {
            "old_chat_id": "oc_e59bd218590bb6abf340b00b4f8b1d5a",
            "status": "not_in_current_app_list",
            "daemon_names": "haitian_test0",
            "registry_name_candidates": "intern_haitian_test0",
            "project_candidates": "OPD",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "OPD:intern_haitian_test0"
        },
        {
            "old_chat_id": "oc_a8b0dbf3630b1833e915e45246a05d71",
            "status": "not_in_current_app_list",
            "daemon_names": "haitian_test1",
            "registry_name_candidates": "intern_haitian_test1",
            "project_candidates": "OPD",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 haitian_test1/OPD",
            "name": "🟢 🤖 haitian_test1/OPD",
            "relay_keys": "OPD:intern_haitian_test1"
        },
        {
            "old_chat_id": "oc_1b20d125c3165933febd8bf8258b2855",
            "status": "not_in_current_app_list",
            "daemon_names": "robomme_explorer;intern_robomme_explorer",
            "registry_name_candidates": "intern_robomme_explorer",
            "project_candidates": "RoboMME_agent",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "RoboMME_agent:intern_robomme_explorer"
        },
        {
            "old_chat_id": "oc_d0855acecfc627bc3b479bacd28d6600",
            "status": "not_in_current_app_list",
            "daemon_names": "test051001",
            "registry_name_candidates": "intern_test051001",
            "project_candidates": "WangRepo",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "WangRepo:intern_test051001"
        },
        {
            "old_chat_id": "oc_fea3deb3a55ac147055d89e5954318c4",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_awm;awm",
            "registry_name_candidates": "intern_awm",
            "project_candidates": "agent-world-model",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "agent-world-model:intern_awm"
        },
        {
            "old_chat_id": "oc_c21445c8df3d72134fdc2e5838ad8f04",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_getdata;getdata",
            "registry_name_candidates": "intern_getdata",
            "project_candidates": "agent_skills",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 getdata/agent_skills",
            "name": "🔴 🤖 getdata/agent_skills",
            "relay_keys": "agent_skills:intern_getdata"
        },
        {
            "old_chat_id": "oc_0dcd36df01a6870a8f2465523e1349f9",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_ltp_feature;ltp_feature",
            "registry_name_candidates": "intern_ltp_feature",
            "project_candidates": "agent_skills",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 ltp_feature/agent_skills",
            "name": "🔴 🤖 ltp_feature/agent_skills",
            "relay_keys": "agent_skills:intern_ltp_feature"
        },
        {
            "old_chat_id": "oc_a14efb707df75cb0154f787d09311b57",
            "status": "not_in_current_app_list",
            "daemon_names": "awe;intern_awe",
            "registry_name_candidates": "intern_awe",
            "project_candidates": "agent_world_env",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 awe/agent_world_env",
            "name": "🟢 🚀 awe/agent_world_env",
            "relay_keys": "agent_world_env:intern_awe"
        },
        {
            "old_chat_id": "oc_63c1e4fdc4d1b68885cafed61f1a4dfa",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_awe_wk2",
            "registry_name_candidates": "intern_awe_wk2",
            "project_candidates": "agent_world_env",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "agent_world_env:intern_awe_wk2"
        },
        {
            "old_chat_id": "oc_8f57206c4a43cd29736c1a6366136a48",
            "status": "not_in_current_app_list",
            "daemon_names": "awe_worker_1",
            "registry_name_candidates": "intern_awe_worker_1",
            "project_candidates": "agent_world_env",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 awe_worker_1/agent_world_env",
            "name": "🟢 🚀 awe_worker_1/agent_world_env",
            "relay_keys": "agent_world_env:intern_awe_worker_1"
        },
        {
            "old_chat_id": "oc_dc85340fd7203439b2ecaa047733e37e",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_awe_worker_2;awe_worker_2",
            "registry_name_candidates": "intern_awe_worker_2",
            "project_candidates": "agent_world_env",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 awe_worker_2/agent_world_env",
            "name": "🟢 🚀 awe_worker_2/agent_world_env",
            "relay_keys": "agent_world_env:intern_awe_worker_2"
        },
        {
            "old_chat_id": "oc_6176c3ef66f324a7b4b18cbf49521203",
            "status": "not_in_current_app_list",
            "daemon_names": "awe_worker_3",
            "registry_name_candidates": "intern_awe_worker_3",
            "project_candidates": "agent_world_env",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 awe_worker_3/agent_world_env",
            "name": "🟢 🚀 awe_worker_3/agent_world_env",
            "relay_keys": "agent_world_env:intern_awe_worker_3"
        },
        {
            "old_chat_id": "oc_07ea76773441c8fc8d9c2c888e48a2de",
            "status": "not_in_current_app_list",
            "daemon_names": "awe_worker_4",
            "registry_name_candidates": "intern_awe_worker_4",
            "project_candidates": "agent_world_env",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 awe_worker_4/agent_world_env",
            "name": "🟢 🚀 awe_worker_4/agent_world_env",
            "relay_keys": "agent_world_env:intern_awe_worker_4"
        },
        {
            "old_chat_id": "oc_9ad74db2a318755301059b1d3e2b2c90",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_agentic_quick_paper_reading;agentic_quick_paper_reading",
            "registry_name_candidates": "intern_agentic_quick_paper_reading",
            "project_candidates": "agentic-paper-reading",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 agentic_quick_paper_reading/agentic-paper-reading",
            "name": "🟢 🚀 agentic_quick_paper_reading/agentic-paper-reading",
            "relay_keys": "agentic-paper-reading:intern_agentic_quick_paper_reading"
        },
        {
            "old_chat_id": "oc_2e7c0c11a627433d92694a9a4f384d64",
            "status": "present",
            "daemon_names": "intern_agentic_search_env_opt",
            "registry_name_candidates": "intern_agentic_search_env_opt",
            "project_candidates": "agentic-paper-reading",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "🔴 intern_agentic_search_env_opt/agentic-paper-reading",
            "relay_keys": "agentic-paper-reading:intern_agentic_search_env_opt"
        },
        {
            "old_chat_id": "oc_8cdc861bc76f4adc9cc43f1e720d0af3",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_eval_analyze",
            "registry_name_candidates": "intern_eval_analyze",
            "project_candidates": "axis-eval-platform",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 eval_analyze/axis-eval-platform",
            "name": "🟢 🚀 eval_analyze/axis-eval-platform",
            "relay_keys": "axis-eval-platform:intern_eval_analyze"
        },
        {
            "old_chat_id": "oc_ff43da2bf911df7168406d5520d8529e",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_maintrack",
            "registry_name_candidates": "intern_maintrack",
            "project_candidates": "axis-eval-platform",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 maintrack/axis-eval-platform",
            "name": "🔴 🚀 maintrack/axis-eval-platform",
            "relay_keys": "axis-eval-platform:intern_maintrack"
        },
        {
            "old_chat_id": "oc_d99f225dc28170fb4d0f56065a3642a1",
            "status": "not_in_current_app_list",
            "daemon_names": "data_alex;intern_data_alex",
            "registry_name_candidates": "intern_data_alex",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 data_alex/axis_embodied_data_pipeline",
            "name": "🟢 🤖 data_alex/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_data_alex",
            "new_chat_id": "oc_9e17890d7337c1b7668e62838c9d948a"
        },
        {
            "old_chat_id": "oc_af34e3fd38ee547b2413eb5b486c61bf",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_data_bridge;data_bridge",
            "registry_name_candidates": "intern_data_bridge",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 data_bridge/axis_embodied_data_pipeline",
            "name": "🟢 🤖 data_bridge/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_data_bridge",
            "new_chat_id": "oc_55278d5c03b4ef0420321093ec677fc8"
        },
        {
            "old_chat_id": "oc_f5db5578134beab3cdd3f902a1c4737d",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_data_cola;data_cola",
            "registry_name_candidates": "intern_data_cola",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 data_cola/axis_embodied_data_pipeline",
            "name": "🟢 🤖 data_cola/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_data_cola",
            "new_chat_id": "oc_7f4fda72ad8af3e34106beaa3a579392"
        },
        {
            "old_chat_id": "oc_bf8ff54febdd79e35b7df7f8491873dc",
            "status": "not_in_current_app_list",
            "daemon_names": "data_dragon;intern_data_dragon",
            "registry_name_candidates": "intern_data_dragon",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 data_dragon/axis_embodied_data_pipeline",
            "name": "🟢 🤖 data_dragon/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_data_dragon",
            "new_chat_id": "oc_cccdf69c82e65c537c1047adce2d75bc"
        },
        {
            "old_chat_id": "oc_d63e6658b4b0e1c2bff44ae698b94d17",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_data_expensive;data_expensive",
            "registry_name_candidates": "intern_data_expensive",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 data_expensive/axis_embodied_data_pipeline",
            "name": "🟢 🤖 data_expensive/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_data_expensive",
            "new_chat_id": "oc_e30024b6df28ce6f97d5b8c9499223e1"
        },
        {
            "old_chat_id": "oc_782b691ebbeb92d1aa713e3ef10ce511",
            "status": "not_in_current_app_list",
            "daemon_names": "data_lead;intern_data_lead",
            "registry_name_candidates": "intern_data_lead",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 data_lead/axis_embodied_data_pipeline",
            "name": "🟢 🤖 data_lead/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_data_lead",
            "new_chat_id": "oc_6a624e841a031f4acafb773965b1481a"
        },
        {
            "old_chat_id": "oc_0d0554271d4fe7a77238854c06a568d3",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_data_milk;data_milk",
            "registry_name_candidates": "intern_data_milk",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 data_milk/axis_embodied_data_pipeline",
            "name": "🟢 🤖 data_milk/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_data_milk",
            "new_chat_id": "oc_1e89dc0deb7a11f52333731be405638b"
        },
        {
            "old_chat_id": "oc_94a777506fce3d0a3eff89cebcb2fc4a",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_data_visual;data_visual",
            "registry_name_candidates": "intern_data_visual",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 data_visual/axis_embodied_data_pipeline",
            "name": "🔴 🤖 data_visual/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_data_visual"
        },
        {
            "old_chat_id": "oc_752a3f303b91627e57323d40c29fed5e",
            "status": "not_in_current_app_list",
            "daemon_names": "joint_data_processor",
            "registry_name_candidates": "intern_joint_data_processor",
            "project_candidates": "axis_embodied_data_pipeline",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 joint_data_processor/axis_embodied_data_pipeline",
            "name": "🟢 🚀 joint_data_processor/axis_embodied_data_pipeline",
            "relay_keys": "axis_embodied_data_pipeline:intern_joint_data_processor"
        },
        {
            "old_chat_id": "oc_fc014c4cac78699be9305063c2b63c44",
            "status": "not_in_current_app_list",
            "daemon_names": "bug_fix",
            "registry_name_candidates": "bug_fix",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:bug_fix"
        },
        {
            "old_chat_id": "oc_94690995f185eb29a9c55bd24e0df8d9",
            "status": "not_in_current_app_list",
            "daemon_names": "intern1",
            "registry_name_candidates": "intern1",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern1"
        },
        {
            "old_chat_id": "oc_b859aac2a7070301ebdb7cf711e185b7",
            "status": "not_in_current_app_list",
            "daemon_names": "add_codex;intern_add_codex",
            "registry_name_candidates": "intern_add_codex",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 add_codex/axis_intern_agents",
            "name": "🔴 🤖 add_codex/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_add_codex"
        },
        {
            "old_chat_id": "oc_9a4262b1972e8779c11708cb3f2d2428",
            "status": "not_in_current_app_list",
            "daemon_names": "add_feature;intern_add_feature",
            "registry_name_candidates": "intern_add_feature",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 add_feature/axis_intern_agents",
            "name": "🔴 🤖 add_feature/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_add_feature"
        },
        {
            "old_chat_id": "oc_503201de7d004da2d7c99048ac3e5e91",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_add_feature2;add_feature2",
            "registry_name_candidates": "intern_add_feature2",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_add_feature2"
        },
        {
            "old_chat_id": "oc_9e49b16e3efd63a7c082db5bf09facb3",
            "status": "not_in_current_app_list",
            "daemon_names": "agsyn_jing",
            "registry_name_candidates": "intern_agsyn_jing",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_agsyn_jing"
        },
        {
            "old_chat_id": "oc_86f0b75a942fc7981acfef51bffa6eab",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_algorithm_optimization;algorithm_optimization",
            "registry_name_candidates": "intern_algorithm_optimization",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_algorithm_optimization"
        },
        {
            "old_chat_id": "oc_63643badb3b6d078f6be93b830c98593",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_arch;arch",
            "registry_name_candidates": "intern_arch",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_arch"
        },
        {
            "old_chat_id": "oc_37eb50a3a0f9f5ee9acfba3e2d358514",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_axis_lead",
            "registry_name_candidates": "intern_axis_lead",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 axis_lead/axis_intern_agents",
            "name": "🟢 🚀 axis_lead/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_axis_lead",
            "new_chat_id": "oc_3406d61604baed1b4ef042c2c2ba3608"
        },
        {
            "old_chat_id": "oc_c87469c403da29838e761a90067e9a53",
            "status": "not_in_current_app_list",
            "daemon_names": "axrl_code_checker",
            "registry_name_candidates": "intern_axrl_code_checker",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_axrl_code_checker"
        },
        {
            "old_chat_id": "oc_ba9394421b816d9fa7e7ff850c9ccc62",
            "status": "not_in_current_app_list",
            "daemon_names": "cephfs_transfer",
            "registry_name_candidates": "intern_cephfs_transfer",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_cephfs_transfer"
        },
        {
            "old_chat_id": "oc_e79054340ec52b7db28dded5083122d7",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_codex_test",
            "registry_name_candidates": "intern_codex_test",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 codex_test/axis_intern_agents",
            "name": "🔴 🚀 codex_test/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_codex_test"
        },
        {
            "old_chat_id": "oc_7bf2b284438724c9566969afb6df9149",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_codex_test2",
            "registry_name_candidates": "intern_codex_test2",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 codex_test2/axis_intern_agents",
            "name": "🔴 🚀 codex_test2/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_codex_test2"
        },
        {
            "old_chat_id": "oc_6afa47bf61220d3538b701ce810231e8",
            "status": "not_in_current_app_list",
            "daemon_names": "common_crawler_local_search_engine_builder",
            "registry_name_candidates": "intern_common_crawler_local_search_engine_builder",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_common_crawler_local_search_engine_builder"
        },
        {
            "old_chat_id": "oc_bd204af15f88132cdaec8e83683d2b72",
            "status": "not_in_current_app_list",
            "daemon_names": "contract",
            "registry_name_candidates": "intern_contract",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_contract"
        },
        {
            "old_chat_id": "oc_d2c33dfa5cd9953f5a67ecbf307ab35f",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_copilot;copilot",
            "registry_name_candidates": "intern_copilot",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_copilot"
        },
        {
            "old_chat_id": "oc_5ec88476e64e265bf69b946477a88f27",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_data;data",
            "registry_name_candidates": "intern_data",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 data/axis_intern_agents",
            "name": "🔴 🤖 data/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_data"
        },
        {
            "old_chat_id": "oc_51f18ab34e2c5407a430dad73edbafa0",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_debug;debug",
            "registry_name_candidates": "intern_debug",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_debug"
        },
        {
            "old_chat_id": "oc_9db2c4b79e350e3161b24b88646b58d7",
            "status": "not_in_current_app_list",
            "daemon_names": "debug_haitian",
            "registry_name_candidates": "intern_debug_haitian",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 debug_haitian/axis_intern_agents",
            "name": "🟢 🚀 debug_haitian/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_debug_haitian"
        },
        {
            "old_chat_id": "oc_df8b59039f04edd6a53d40b52148bfdf",
            "status": "not_in_current_app_list",
            "daemon_names": "debug_jingjing",
            "registry_name_candidates": "intern_debug_jingjing",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 debug_jingjing/axis_intern_agents",
            "name": "🟢 🤖 debug_jingjing/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_debug_jingjing"
        },
        {
            "old_chat_id": "oc_97780d94ee3f965927c2412cb01cf15e",
            "status": "not_in_current_app_list",
            "daemon_names": "debug_xiaohan",
            "registry_name_candidates": "intern_debug_xiaohan",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_debug_xiaohan"
        },
        {
            "old_chat_id": "oc_a821a6a6b32972b979a769afe121674f",
            "status": "not_in_current_app_list",
            "daemon_names": "debug_yuhao",
            "registry_name_candidates": "intern_debug_yuhao",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 debug_yuhao/axis_intern_agents",
            "name": "🟢 🚀 debug_yuhao/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_debug_yuhao"
        },
        {
            "old_chat_id": "oc_f67e637f5f0489bcfa1d39faf0e8438a",
            "status": "not_in_current_app_list",
            "daemon_names": "dedup_agent",
            "registry_name_candidates": "intern_dedup_agent",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_dedup_agent"
        },
        {
            "old_chat_id": "oc_05df374f983dcb5353f72283c4670932",
            "status": "not_in_current_app_list",
            "daemon_names": "dns;intern_dns",
            "registry_name_candidates": "intern_dns",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 dns/axis_intern_agents",
            "name": "🔴 🚀 dns/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_dns"
        },
        {
            "old_chat_id": "oc_b4562db4b9b1f87c1cdaa4eb4ff7ab6c",
            "status": "not_in_current_app_list",
            "daemon_names": "experiment_run;intern_experiment_run",
            "registry_name_candidates": "intern_experiment_run",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_experiment_run"
        },
        {
            "old_chat_id": "oc_7eec15942e9ec972eb001f77c82ee11b",
            "status": "not_in_current_app_list",
            "daemon_names": "fla;intern_fla",
            "registry_name_candidates": "intern_fla",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_fla"
        },
        {
            "old_chat_id": "oc_c366becda5a8fae843d48493d8fb80ea",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_flame_copilot;flame_copilot",
            "registry_name_candidates": "intern_flame_copilot",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_flame_copilot"
        },
        {
            "old_chat_id": "oc_7e53d13f9d9bbc83d961ed994e937e7f",
            "status": "not_in_current_app_list",
            "daemon_names": "flame_perf;intern_flame_perf",
            "registry_name_candidates": "intern_flame_perf",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_flame_perf"
        },
        {
            "old_chat_id": "oc_03712dcaff5e013ce16a30ac87c5b54c",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_jing_medunia",
            "registry_name_candidates": "intern_jing_medunia",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_jing_medunia"
        },
        {
            "old_chat_id": "oc_fb2ee4ce1d18ea4136cc54c6cbf9b951",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_jingjing_medagent;jingjing_medagent",
            "registry_name_candidates": "intern_jingjing_medagent",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_jingjing_medagent"
        },
        {
            "old_chat_id": "oc_1fca19ae4987d8456d48f0ac9b185490",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_keep_space_test;keep_space_test",
            "registry_name_candidates": "intern_keep_space_test",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_keep_space_test"
        },
        {
            "old_chat_id": "oc_e5b69ec4f05dcd612f80ed78d83ac19d",
            "status": "not_in_current_app_list",
            "daemon_names": "lijiang;intern_lijiang",
            "registry_name_candidates": "intern_lijiang",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_lijiang"
        },
        {
            "old_chat_id": "oc_0c7f3b737aa35633d9c4c1716c8db255",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_llavaov_job_runner;llavaov_job_runner",
            "registry_name_candidates": "intern_llavaov_job_runner",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_llavaov_job_runner"
        },
        {
            "old_chat_id": "oc_db78c83d88f6ed0496fd2d3f740b91c9",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_longrun;longrun",
            "registry_name_candidates": "intern_longrun",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_longrun"
        },
        {
            "old_chat_id": "oc_541aec7b4047aca0037c436232cc4dd6",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_ltp_scripts;ltp_scripts",
            "registry_name_candidates": "intern_ltp_scripts",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_ltp_scripts"
        },
        {
            "old_chat_id": "oc_2166f03774e624fce0ad2a4e2f754c01",
            "status": "not_in_current_app_list",
            "daemon_names": "ltp_tianyang",
            "registry_name_candidates": "intern_ltp_tianyang",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_ltp_tianyang"
        },
        {
            "old_chat_id": "oc_67fd676491c8fd1d384eed2f95b88408",
            "status": "not_in_current_app_list",
            "daemon_names": "max;intern_max",
            "registry_name_candidates": "intern_max",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_max"
        },
        {
            "old_chat_id": "oc_7bf97610decc8ab7cc6c7060b9b6f6b4",
            "status": "not_in_current_app_list",
            "daemon_names": "megatron_leader;intern_megatron_leader",
            "registry_name_candidates": "intern_megatron_leader",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_megatron_leader"
        },
        {
            "old_chat_id": "oc_3054ca9102d3df7e4b2072a2d7efa57d",
            "status": "not_in_current_app_list",
            "daemon_names": "midtrain_data",
            "registry_name_candidates": "intern_midtrain_data",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_midtrain_data"
        },
        {
            "old_chat_id": "oc_df4406ffaf096ed0337d5af4666fae50",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_min_test;min_test",
            "registry_name_candidates": "intern_min_test",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 min_test/axis_intern_agents",
            "name": "🟢 🤖 min_test/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_min_test",
            "new_chat_id": "oc_cb463673c0f1b02d850d0b266ecbb66f"
        },
        {
            "old_chat_id": "oc_cb07632973d416218d003478bf01ff03",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_outer_cli",
            "registry_name_candidates": "intern_outer_cli",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 outer_cli/axis_intern_agents",
            "name": "🟢 🚀 outer_cli/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_outer_cli",
            "new_chat_id": "oc_eec8b76298ff99cc052af0dff0caecc7"
        },
        {
            "old_chat_id": "oc_d3cc3ec95d258df6b6fdb05982c4840d",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_outer_helper",
            "registry_name_candidates": "intern_outer_helper",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 outer_helper/axis_intern_agents",
            "name": "🟢 🚀 outer_helper/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_outer_helper",
            "new_chat_id": "oc_c64ee8a843910e6a3cbc0d675f6cee1f"
        },
        {
            "old_chat_id": "oc_74e016d6abaec487e63858a702ed1831",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_outer_hooks",
            "registry_name_candidates": "intern_outer_hooks",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 outer_hooks/axis_intern_agents",
            "name": "🟢 🚀 outer_hooks/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_outer_hooks",
            "new_chat_id": "oc_1685ff40424ed11a79bd5174dfbdcf49"
        },
        {
            "old_chat_id": "oc_b4c2e627fdc11da3e952811e9b46e6f2",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_outer_publish",
            "registry_name_candidates": "intern_outer_publish",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 outer_publish/axis_intern_agents",
            "name": "🟢 🚀 outer_publish/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_outer_publish",
            "new_chat_id": "oc_5720814d4620ded1a45a5a3364fbff64"
        },
        {
            "old_chat_id": "oc_2d28c61de8e4595d540d8727c3c7e3fa",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_outer_workspace",
            "registry_name_candidates": "intern_outer_workspace",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 outer_workspace/axis_intern_agents",
            "name": "🟢 🚀 outer_workspace/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_outer_workspace",
            "new_chat_id": "oc_23f05570ecd09e99f628ee6653b785c7"
        },
        {
            "old_chat_id": "oc_14638647e493610e2b66e0341988e2ea",
            "status": "not_in_current_app_list",
            "daemon_names": "paper_chilly;intern_paper_chilly",
            "registry_name_candidates": "intern_paper_chilly",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_paper_chilly"
        },
        {
            "old_chat_id": "oc_b0b06f001a1ad7321deeac261904eb4a",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_paper_dog",
            "registry_name_candidates": "intern_paper_dog",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_paper_dog"
        },
        {
            "old_chat_id": "oc_cb6b1a89e47589ba4da3df80d34e7287",
            "status": "not_in_current_app_list",
            "daemon_names": "pixel_agent;intern_pixel_agent",
            "registry_name_candidates": "intern_pixel_agent",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 pixel_agent/axis_intern_agents",
            "name": "🟢 🤖 pixel_agent/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_pixel_agent",
            "new_chat_id": "oc_245371e6b7e818364df2d72bdef2b8a0"
        },
        {
            "old_chat_id": "oc_9b7f23b6f673074a37e9036e82cef361",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_product_publish;product_publish",
            "registry_name_candidates": "intern_product_publish",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 product_publish/axis_intern_agents",
            "name": "🟢 🚀 product_publish/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_product_publish",
            "new_chat_id": "oc_73015a6209cf1090365414720e8f076e"
        },
        {
            "old_chat_id": "oc_607cff31630c6e9c477493faa26acd24",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_research_evaluation_frontee",
            "registry_name_candidates": "intern_research_evaluation_frontee",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_research_evaluation_frontee"
        },
        {
            "old_chat_id": "oc_9d6af01fd625cb5365d97cd5b8b0db25",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_research_evaluation_joke",
            "registry_name_candidates": "intern_research_evaluation_joke",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_research_evaluation_joke"
        },
        {
            "old_chat_id": "oc_dcab701020dd93a0b0b46572ba905e2d",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_research_evaluation_optim",
            "registry_name_candidates": "intern_research_evaluation_optim",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_research_evaluation_optim"
        },
        {
            "old_chat_id": "oc_a4213ce7ad6d02bf494fb84d812a20ba",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_robomme_explore",
            "registry_name_candidates": "intern_robomme_explore",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_robomme_explore"
        },
        {
            "old_chat_id": "oc_471ec036d1ca9add68ea7fdefc3845ca",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_robomme_explore_2",
            "registry_name_candidates": "intern_robomme_explore_2",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_robomme_explore_2"
        },
        {
            "old_chat_id": "oc_881f1fe9409316468dc267e589ac01fd",
            "status": "not_in_current_app_list",
            "daemon_names": "rongwei_debug",
            "registry_name_candidates": "intern_rongwei_debug",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 rongwei_debug/axis_intern_agents",
            "name": "🟢 🤖 rongwei_debug/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rongwei_debug"
        },
        {
            "old_chat_id": "oc_db173977a7376cbf7e972a099495e48e",
            "status": "not_in_current_app_list",
            "daemon_names": "rongwei_gittest",
            "registry_name_candidates": "intern_rongwei_gittest",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_rongwei_gittest"
        },
        {
            "old_chat_id": "oc_6e73cf9ec5be27269cf913c590430562",
            "status": "not_in_current_app_list",
            "daemon_names": "rui;intern_rui",
            "registry_name_candidates": "intern_rui",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 rui/axis_intern_agents",
            "name": "🔴 🤖 rui/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rui"
        },
        {
            "old_chat_id": "oc_bc7332f043b50699845c48f5f0594744",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rule",
            "registry_name_candidates": "intern_rule",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_rule"
        },
        {
            "old_chat_id": "oc_84a9fddd2a4a749a4af2198f7c4cd8b8",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rule_alice;rule_alice",
            "registry_name_candidates": "intern_rule_alice",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 rule_alice/axis_intern_agents",
            "name": "🟢 🤖 rule_alice/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rule_alice",
            "new_chat_id": "oc_fb52874519d23918cff5a7b00e355ab2"
        },
        {
            "old_chat_id": "oc_fd76a8d26dd537ad17dc37f58a9e355e",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rule_bob",
            "registry_name_candidates": "intern_rule_bob",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 rule_bob/axis_intern_agents",
            "name": "🟢 🤖 rule_bob/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rule_bob",
            "new_chat_id": "oc_d47bbc657a22c56e81adc3228cb487c4"
        },
        {
            "old_chat_id": "oc_74ab25f4d5cac568c5dd76639d18bd5e",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rule_cela",
            "registry_name_candidates": "intern_rule_cela",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_rule_cela"
        },
        {
            "old_chat_id": "oc_657e2d208b2d702f784c4bb98e707d1d",
            "status": "not_in_current_app_list",
            "daemon_names": "rule_claude;intern_rule_claude",
            "registry_name_candidates": "intern_rule_claude",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 rule_claude/axis_intern_agents",
            "name": "🟢 🤖 rule_claude/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rule_claude",
            "new_chat_id": "oc_4bac9cda2b6489b50e6e021dcc70f1ba"
        },
        {
            "old_chat_id": "oc_1702eeed6044d65d6128a5ce7d2a058d",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rule_debug;rule_debug",
            "registry_name_candidates": "intern_rule_debug",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 rule_debug/axis_intern_agents",
            "name": "🔴 🤖 rule_debug/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rule_debug"
        },
        {
            "old_chat_id": "oc_1097a565264cba62a9b95c2ec3552afd",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rule_euler;rule_euler",
            "registry_name_candidates": "intern_rule_euler",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 rule_euler/axis_intern_agents",
            "name": "🟢 🤖 rule_euler/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rule_euler",
            "new_chat_id": "oc_7d360e6f54856a279121c766cfd50345"
        },
        {
            "old_chat_id": "oc_2cb1ef39868f1c2567e5b3a90bc956d7",
            "status": "not_in_current_app_list",
            "daemon_names": "rule_fox;intern_rule_fox",
            "registry_name_candidates": "intern_rule_fox",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 rule_fox/axis_intern_agents",
            "name": "🟢 🤖 rule_fox/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rule_fox",
            "new_chat_id": "oc_f389ee4026f3253c354b5358f8c3bd9e"
        },
        {
            "old_chat_id": "oc_296faca6e0e1dbbd9661c6458a795354",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rule_held",
            "registry_name_candidates": "intern_rule_held",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_rule_held"
        },
        {
            "old_chat_id": "oc_243893c110b2f54f146eff9c5ff49bf5",
            "status": "not_in_current_app_list",
            "daemon_names": "rule_inst;intern_rule_inst",
            "registry_name_candidates": "intern_rule_inst",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 rule_inst/axis_intern_agents",
            "name": "🔴 🚀 rule_inst/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rule_inst"
        },
        {
            "old_chat_id": "oc_b3df52cd16ea99fd10e438f9dcd46b8a",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rule_juice;rule_juice",
            "registry_name_candidates": "intern_rule_juice",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 rule_juice/axis_intern_agents",
            "name": "🟢 🚀 rule_juice/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_rule_juice",
            "new_chat_id": "oc_75e7d095f52783f72f8b198debb2f3e7"
        },
        {
            "old_chat_id": "oc_b40c21db2b4b0dc5ac451c06819f10e4",
            "status": "not_in_current_app_list",
            "daemon_names": "rule_kaggle;intern_rule_kaggle",
            "registry_name_candidates": "intern_rule_kaggle",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_rule_kaggle"
        },
        {
            "old_chat_id": "oc_90f29a1338f542f37a655a320b96cb50",
            "status": "not_in_current_app_list",
            "daemon_names": "songlei_debug",
            "registry_name_candidates": "intern_songlei_debug",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 songlei_debug/axis_intern_agents",
            "name": "🔴 🤖 songlei_debug/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_songlei_debug"
        },
        {
            "old_chat_id": "oc_82ecc33c14b510da894f978619d98f60",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_srl_debug",
            "registry_name_candidates": "intern_srl_debug",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_srl_debug"
        },
        {
            "old_chat_id": "oc_c28dcb5f85ea707c87573847946ac744",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_test_shizhao",
            "registry_name_candidates": "intern_test_shizhao",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_test_shizhao"
        },
        {
            "old_chat_id": "oc_5ff15350aed9601ee90080320b6f69d4",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_tianyu_he",
            "registry_name_candidates": "intern_tianyu_he",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_tianyu_he"
        },
        {
            "old_chat_id": "oc_01b6c2e44eb10c9249b18fab3afcba08",
            "status": "not_in_current_app_list",
            "daemon_names": "vibe_coding",
            "registry_name_candidates": "intern_vibe_coding",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_vibe_coding"
        },
        {
            "old_chat_id": "oc_0b69fc1b00eacf04f4eea2516d243b87",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_vlmeval",
            "registry_name_candidates": "intern_vlmeval",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_vlmeval"
        },
        {
            "old_chat_id": "oc_316e15145a45066a2d65d152d4a6db5a",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_xu_debug",
            "registry_name_candidates": "intern_xu_debug",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_xu_debug"
        },
        {
            "old_chat_id": "oc_d24191ab8ceff9b018007a34495bcb5d",
            "status": "not_in_current_app_list",
            "daemon_names": "xu_debug_2",
            "registry_name_candidates": "intern_xu_debug_2",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_xu_debug_2"
        },
        {
            "old_chat_id": "oc_ffd05471afb9a01c7c8a755fa93fe3b1",
            "status": "not_in_current_app_list",
            "daemon_names": "xu_new_feature_claude",
            "registry_name_candidates": "intern_xu_new_feature_claude",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 xu_new_feature_claude/axis_intern_agents",
            "name": "🔴 🤖 xu_new_feature_claude/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_xu_new_feature_claude"
        },
        {
            "old_chat_id": "oc_55001e06a795e7df03be5db67d8de01d",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_xyl_copilot",
            "registry_name_candidates": "intern_xyl_copilot",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_xyl_copilot"
        },
        {
            "old_chat_id": "oc_a5870c62fff420fd3d416b904d23647c",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_xyp9x",
            "registry_name_candidates": "intern_xyp9x",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_xyp9x"
        },
        {
            "old_chat_id": "oc_9ffbb8471182af60ba5138841c166a89",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_yang",
            "registry_name_candidates": "intern_yang",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "codex",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:intern_yang"
        },
        {
            "old_chat_id": "oc_5abbb3cffd837878095eb63951068008",
            "status": "not_in_current_app_list",
            "daemon_names": "yang_bugfix;intern_yang_bugfix",
            "registry_name_candidates": "intern_yang_bugfix",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 yang_bugfix/axis_intern_agents",
            "name": "🔴 🤖 yang_bugfix/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_yang_bugfix"
        },
        {
            "old_chat_id": "oc_12c594ade8e84eb8e71d873651789a1e",
            "status": "not_in_current_app_list",
            "daemon_names": "yf_debug",
            "registry_name_candidates": "intern_yf_debug",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 yf_debug/axis_intern_agents",
            "name": "🟢 🤖 yf_debug/axis_intern_agents",
            "relay_keys": "axis_intern_agents:intern_yf_debug"
        },
        {
            "old_chat_id": "oc_4fb24bf35cd6696acb4d1b4bb1851f7a",
            "status": "present",
            "daemon_names": "llava_bugfix",
            "registry_name_candidates": "llava_bugfix",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "🔴 llava_bugfix/LLaVA-OneVision-1.5",
            "relay_keys": "axis_intern_agents:llava_bugfix"
        },
        {
            "old_chat_id": "oc_4ec612228460514f72e80ff03d0627fb",
            "status": "not_in_current_app_list",
            "daemon_names": "new_intern",
            "registry_name_candidates": "new_intern",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:new_intern"
        },
        {
            "old_chat_id": "oc_bd6cba97cd6688fc952c4710ab095ca3",
            "status": "not_in_current_app_list",
            "daemon_names": "task200_probe",
            "registry_name_candidates": "task200_probe",
            "project_candidates": "axis_intern_agents",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_intern_agents:task200_probe"
        },
        {
            "old_chat_id": "oc_d26b963d5a8ce7aaf00d1b20e2dc5059",
            "status": "not_in_current_app_list",
            "daemon_names": "sft_a",
            "registry_name_candidates": "intern_sft_a",
            "project_candidates": "axis_intern_agents;ltp-LLamaFactory",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "ltp-LLamaFactory:intern_sft_a;axis_intern_agents:intern_sft_a"
        },
        {
            "old_chat_id": "oc_4c65c8ec6cf3c8079cc957570a9c2526",
            "status": "not_in_current_app_list",
            "daemon_names": "archive_icml",
            "registry_name_candidates": "intern_archive_icml",
            "project_candidates": "axis_intern_agents;lupaper",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "lupaper:intern_archive_icml;axis_intern_agents:intern_archive_icml"
        },
        {
            "old_chat_id": "oc_2203f419fbfb540b302acbc39bc5baa5",
            "status": "not_in_current_app_list",
            "daemon_names": "intern2",
            "registry_name_candidates": "intern2",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 intern2/axis_vla",
            "name": "🔴 🤖 intern2/axis_vla",
            "relay_keys": "axis_vla:intern2"
        },
        {
            "old_chat_id": "oc_921fc36ed4c745cd05c9ee0788225661",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_axis_ft;axis_ft",
            "registry_name_candidates": "intern_axis_ft",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 axis_ft/axis_vla",
            "name": "🟢 🤖 axis_ft/axis_vla",
            "relay_keys": "axis_vla:intern_axis_ft"
        },
        {
            "old_chat_id": "oc_a1a9c24bd8b51d8d31f22029f5fcf7bd",
            "status": "not_in_current_app_list",
            "daemon_names": "calvin_pi05rlinf_verify_xiangyu;intern_calvin_pi05rlinf_verify_xiangyu",
            "registry_name_candidates": "intern_calvin_pi05rlinf_verify_xiangyu",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 calvin_pi05rlinf_verify_xiangyu/axis_vla",
            "name": "🟢 🤖 calvin_pi05rlinf_verify_xiangyu/axis_vla",
            "relay_keys": "axis_vla:intern_calvin_pi05rlinf_verify_xiangyu"
        },
        {
            "old_chat_id": "oc_c576205d03c7dfeca06418cc0d9cb98f",
            "status": "not_in_current_app_list",
            "daemon_names": "calvin_stable_xiangyu",
            "registry_name_candidates": "intern_calvin_stable_xiangyu",
            "project_candidates": "axis_vla",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 calvin_stable_xiangyu/axis_vla",
            "name": "🟢 🚀 calvin_stable_xiangyu/axis_vla",
            "relay_keys": "axis_vla:intern_calvin_stable_xiangyu"
        },
        {
            "old_chat_id": "oc_f27f7b352d391f32681e21273faa1e68",
            "status": "not_in_current_app_list",
            "daemon_names": "calvin_xiangyu;intern_calvin_xiangyu",
            "registry_name_candidates": "intern_calvin_xiangyu",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 calvin_xiangyu/axis_vla",
            "name": "🟢 🤖 calvin_xiangyu/axis_vla",
            "relay_keys": "axis_vla:intern_calvin_xiangyu"
        },
        {
            "old_chat_id": "oc_4d352cdc1d49420301e22fa0621ab90c",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_dev1;dev1",
            "registry_name_candidates": "intern_dev1",
            "project_candidates": "axis_vla",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_vla:intern_dev1"
        },
        {
            "old_chat_id": "oc_889ac2e8209768e3b6c7d8b7c6598a28",
            "status": "not_in_current_app_list",
            "daemon_names": "norm_computation;intern_norm_computation",
            "registry_name_candidates": "intern_norm_computation",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 norm_computation/axis_vla",
            "name": "🟢 🤖 norm_computation/axis_vla",
            "relay_keys": "axis_vla:intern_norm_computation"
        },
        {
            "old_chat_id": "oc_d7099883271817a924286aaea7592063",
            "status": "not_in_current_app_list",
            "daemon_names": "pi05_libero_eval;intern_pi05_libero_eval",
            "registry_name_candidates": "intern_pi05_libero_eval",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 pi05_libero_eval/axis_vla",
            "name": "🟢 🤖 pi05_libero_eval/axis_vla",
            "relay_keys": "axis_vla:intern_pi05_libero_eval"
        },
        {
            "old_chat_id": "oc_2dc4ab40d5e9863e4afba3adc90c4641",
            "status": "not_in_current_app_list",
            "daemon_names": "pi05master_calvin_32norm_xiangyu",
            "registry_name_candidates": "intern_pi05master_calvin_32norm_xiangyu",
            "project_candidates": "axis_vla",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axis_vla:intern_pi05master_calvin_32norm_xiangyu"
        },
        {
            "old_chat_id": "oc_0f6cfbf4b53b07c3d875de3f94629a82",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_pi05master_xiangyu",
            "registry_name_candidates": "intern_pi05master_xiangyu",
            "project_candidates": "axis_vla",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 pi05master_xiangyu/axis_vla",
            "name": "🟢 🚀 pi05master_xiangyu/axis_vla",
            "relay_keys": "axis_vla:intern_pi05master_xiangyu"
        },
        {
            "old_chat_id": "oc_0834bfcac758ba0cac4bae10ebec3d5e",
            "status": "not_in_current_app_list",
            "daemon_names": "pretrain;intern_pretrain",
            "registry_name_candidates": "intern_pretrain",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 pretrain/axis_vla",
            "name": "🟢 🤖 pretrain/axis_vla",
            "relay_keys": "axis_vla:intern_pretrain"
        },
        {
            "old_chat_id": "oc_cf2582a9a8e14bce2e9b58e0cac4cfa4",
            "status": "not_in_current_app_list",
            "daemon_names": "pretrain_ch;intern_pretrain_ch",
            "registry_name_candidates": "intern_pretrain_ch",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 pretrain_ch/axis_vla",
            "name": "🔴 🤖 pretrain_ch/axis_vla",
            "relay_keys": "axis_vla:intern_pretrain_ch"
        },
        {
            "old_chat_id": "oc_c169437ae14a957a66d489ffc08940cd",
            "status": "present",
            "daemon_names": "pretrain_norm_expert",
            "registry_name_candidates": "intern_pretrain_norm_expert",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 pretrain_norm_expert/axis_vla",
            "name": "🟢 🤖 pretrain_norm_expert/axis_vla",
            "relay_keys": "axis_vla:intern_pretrain_norm_expert"
        },
        {
            "old_chat_id": "oc_ad6e820e0703e3d04222e83187259547",
            "status": "not_in_current_app_list",
            "daemon_names": "research_evaluation;intern_research_evaluation",
            "registry_name_candidates": "intern_research_evaluation",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 research_evaluation/axis_vla",
            "name": "🔴 🤖 research_evaluation/axis_vla",
            "relay_keys": "axis_vla:intern_research_evaluation"
        },
        {
            "old_chat_id": "oc_2e7fb72c3677d48a51911eaaeb32e42a",
            "status": "not_in_current_app_list",
            "daemon_names": "robotwin_xiangyu;intern_robotwin_xiangyu",
            "registry_name_candidates": "intern_robotwin_xiangyu",
            "project_candidates": "axis_vla",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 robotwin_xiangyu/axis_vla",
            "name": "🟢 🚀 robotwin_xiangyu/axis_vla",
            "relay_keys": "axis_vla:intern_robotwin_xiangyu"
        },
        {
            "old_chat_id": "oc_18a3bbf7a38a53ba47b1ead0f6db4bfb",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_tw_pi05_libero_eval",
            "registry_name_candidates": "intern_tw_pi05_libero_eval",
            "project_candidates": "axis_vla",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 tw_pi05_libero_eval/axis_vla",
            "name": "🟢 🚀 tw_pi05_libero_eval/axis_vla",
            "relay_keys": "axis_vla:intern_tw_pi05_libero_eval"
        },
        {
            "old_chat_id": "oc_489075044fd9c18bcbf15428aab3d10f",
            "status": "not_in_current_app_list",
            "daemon_names": "tw_pretrain;intern_tw_pretrain",
            "registry_name_candidates": "intern_tw_pretrain",
            "project_candidates": "axis_vla",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 tw_pretrain/axis_vla",
            "name": "🟢 🚀 tw_pretrain/axis_vla",
            "relay_keys": "axis_vla:intern_tw_pretrain"
        },
        {
            "old_chat_id": "oc_763681902c5da30ce927950c5cc27ef2",
            "status": "not_in_current_app_list",
            "daemon_names": "vla_lead;intern_vla_lead",
            "registry_name_candidates": "intern_vla_lead",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 vla_lead/axis_vla",
            "name": "🔴 🤖 vla_lead/axis_vla",
            "relay_keys": "axis_vla:intern_vla_lead"
        },
        {
            "old_chat_id": "oc_3914b78d3d06cffb107c13bf4519be4c",
            "status": "not_in_current_app_list",
            "daemon_names": "xiangyu_qatest;intern_xiangyu_qatest",
            "registry_name_candidates": "intern_xiangyu_qatest",
            "project_candidates": "axis_vla",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 xiangyu_qatest/axis_vla",
            "name": "🟢 🚀 xiangyu_qatest/axis_vla",
            "relay_keys": "axis_vla:intern_xiangyu_qatest"
        },
        {
            "old_chat_id": "oc_7cd71fc8801ef19a428462c16e1b69f2",
            "status": "not_in_current_app_list",
            "daemon_names": "yb",
            "registry_name_candidates": "intern_yb",
            "project_candidates": "axis_vla",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 yb/axis_vla",
            "name": "🔴 🤖 yb/axis_vla",
            "relay_keys": "axis_vla:intern_yb"
        },
        {
            "old_chat_id": "oc_c93a4ead604b7576e5d0901a06e6bd3a",
            "status": "not_in_current_app_list",
            "daemon_names": "openai_anthropic_crawl",
            "registry_name_candidates": "intern_openai_anthropic_crawl",
            "project_candidates": "axrd",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axrd:intern_openai_anthropic_crawl"
        },
        {
            "old_chat_id": "oc_e2038e7209505d7271a057a2f8126965",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_rd_runner",
            "registry_name_candidates": "intern_rd_runner",
            "project_candidates": "axrd",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 rd_runner/axrd",
            "name": "🟢 🚀 rd_runner/axrd",
            "relay_keys": "axrd:intern_rd_runner"
        },
        {
            "old_chat_id": "oc_ae4647e6c28cda1e124dd83888b1bb21",
            "status": "not_in_current_app_list",
            "daemon_names": "summarizer_axrd",
            "registry_name_candidates": "intern_summarizer_axrd",
            "project_candidates": "axrd",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 summarizer_axrd/axrd",
            "name": "🟢 🚀 summarizer_axrd/axrd",
            "relay_keys": "axrd:intern_summarizer_axrd"
        },
        {
            "old_chat_id": "oc_2df4290e777d2eaa63ae47c6f7963554",
            "status": "not_in_current_app_list",
            "daemon_names": "ui_axrd",
            "registry_name_candidates": "intern_ui_axrd",
            "project_candidates": "axrd",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 ui_axrd/axrd",
            "name": "🟢 🤖 ui_axrd/axrd",
            "relay_keys": "axrd:intern_ui_axrd"
        },
        {
            "old_chat_id": "oc_9da9690d3ab49e35566742ece3541123",
            "status": "not_in_current_app_list",
            "daemon_names": "miro",
            "registry_name_candidates": "intern_agentic_env_code_review",
            "project_candidates": "axrl-agentic-env",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 agentic_env_code_review/axrl-agentic-env",
            "name": "🔴 🚀 agentic_env_code_review/axrl-agentic-env",
            "relay_keys": "axrl-agentic-env:intern_agentic_env_code_review"
        },
        {
            "old_chat_id": "oc_e95b28c2b0e0f7faa96c9bf7b7751e5c",
            "status": "not_in_current_app_list",
            "daemon_names": "agentic_env_review_cc",
            "registry_name_candidates": "intern_agentic_env_review_cc",
            "project_candidates": "axrl-agentic-env",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 agentic_env_review_cc/axrl-agentic-env",
            "name": "🔴 🤖 agentic_env_review_cc/axrl-agentic-env",
            "relay_keys": "axrl-agentic-env:intern_agentic_env_review_cc"
        },
        {
            "old_chat_id": "oc_b52984df53a52f869f7b9c8894ade752",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_agentic_env_v4_debug;agentic_env_v4_debug",
            "registry_name_candidates": "intern_agentic_env_v4_debug",
            "project_candidates": "axrl-agentic-env",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 agentic_env_v4_debug/axrl-agentic-env",
            "name": "🔴 🚀 agentic_env_v4_debug/axrl-agentic-env",
            "relay_keys": "axrl-agentic-env:intern_agentic_env_v4_debug"
        },
        {
            "old_chat_id": "oc_33f42602458117586936c916f8471667",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_agentic_search_env_evolve",
            "registry_name_candidates": "intern_agentic_search_env_evolve",
            "project_candidates": "axrl-agentic-env",
            "type_candidates": "codex",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "axrl-agentic-env:intern_agentic_search_env_evolve"
        },
        {
            "old_chat_id": "oc_3ae5266544c181632c1280519645f8e2",
            "status": "not_in_current_app_list",
            "daemon_names": "benchmark_coordinator",
            "registry_name_candidates": "intern_benchmark_coordinator",
            "project_candidates": "benchmark",
            "type_candidates": "codex",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "benchmark:intern_benchmark_coordinator"
        },
        {
            "old_chat_id": "oc_34098008de26abb647bc07f355b0b528",
            "status": "not_in_current_app_list",
            "daemon_names": "flame;intern_flame",
            "registry_name_candidates": "intern_flame",
            "project_candidates": "flame",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 flame/flame",
            "name": "🔴 🤖 flame/flame",
            "relay_keys": "flame:intern_flame"
        },
        {
            "old_chat_id": "oc_265259b3e07f5c750c332e7cd6f982c5",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_flame_benchmark",
            "registry_name_candidates": "intern_flame_benchmark",
            "project_candidates": "flame",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 flame_benchmark/flame",
            "name": "🔴 🚀 flame_benchmark/flame",
            "relay_keys": "flame:intern_flame_benchmark"
        },
        {
            "old_chat_id": "oc_39b982c039512b121a1f1a02c52ac22d",
            "status": "not_in_current_app_list",
            "daemon_names": "flame_claude;intern_flame_claude",
            "registry_name_candidates": "intern_flame_claude",
            "project_candidates": "flame",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "flame:intern_flame_claude"
        },
        {
            "old_chat_id": "oc_1635f5e54d1976b5b2560cc015ef3204",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_flame_claude2;flame_claude2",
            "registry_name_candidates": "intern_flame_claude2",
            "project_candidates": "flame",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "flame:intern_flame_claude2"
        },
        {
            "old_chat_id": "oc_29f64d7cca4be327f0630b1c26145e02",
            "status": "not_in_current_app_list",
            "daemon_names": "flame_codex2",
            "registry_name_candidates": "intern_flame_codex2",
            "project_candidates": "flame",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 flame_codex2/flame",
            "name": "🔴 🚀 flame_codex2/flame",
            "relay_keys": "flame:intern_flame_codex2"
        },
        {
            "old_chat_id": "oc_3ca5d7c1e645ef7438e3df09c519388b",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_flame_eval",
            "registry_name_candidates": "intern_flame_eval",
            "project_candidates": "flame",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 flame_eval/flame",
            "name": "🔴 🚀 flame_eval/flame",
            "relay_keys": "flame:intern_flame_eval"
        },
        {
            "old_chat_id": "oc_382e4b31ff53a3fce27906e1564e0168",
            "status": "not_in_current_app_list",
            "daemon_names": "baseline_explorer",
            "registry_name_candidates": "intern_baseline_explorer",
            "project_candidates": "gated-memory-policy",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 baseline_explorer/gated-memory-policy",
            "name": "🔴 🤖 baseline_explorer/gated-memory-policy",
            "relay_keys": "gated-memory-policy:intern_baseline_explorer"
        },
        {
            "old_chat_id": "oc_7c041f147352c22610bbe97dea21c292",
            "status": "not_in_current_app_list",
            "daemon_names": "agent_debug_feedback",
            "registry_name_candidates": "intern_agent_debug_feedback",
            "project_candidates": "intern-test",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 agent_debug_feedback/intern-test",
            "name": "🟢 🤖 agent_debug_feedback/intern-test",
            "relay_keys": "intern-test:intern_agent_debug_feedback"
        },
        {
            "old_chat_id": "oc_676afcaa2889dcda090a720b1f98e353",
            "status": "not_in_current_app_list",
            "daemon_names": "paper_abandon;intern_paper_abandon",
            "registry_name_candidates": "intern_paper_abandon",
            "project_candidates": "intern_paper_reading",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 paper_abandon/intern_paper_reading",
            "name": "🔴 🤖 paper_abandon/intern_paper_reading",
            "relay_keys": "intern_paper_reading:intern_paper_abandon",
            "new_chat_id": "oc_737774d5e367510d4340cd1bd94ed7c8"
        },
        {
            "old_chat_id": "oc_662866c43df528e372a61838fca38cba",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_paper_agentic;paper_agentic",
            "registry_name_candidates": "intern_paper_agentic",
            "project_candidates": "intern_paper_reading",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 paper_agentic/intern_paper_reading",
            "name": "🟢 🚀 paper_agentic/intern_paper_reading",
            "relay_keys": "intern_paper_reading:intern_paper_agentic"
        },
        {
            "old_chat_id": "oc_5d39368dea940240572dd22f6f9a88a7",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_paper_boom;paper_boom",
            "registry_name_candidates": "intern_paper_boom",
            "project_candidates": "intern_paper_reading",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 paper_boom/intern_paper_reading",
            "name": "🔴 🤖 paper_boom/intern_paper_reading",
            "relay_keys": "intern_paper_reading:intern_paper_boom"
        },
        {
            "old_chat_id": "oc_ffc4aac5f1f064fb0d413c05ac4e9149",
            "status": "not_in_current_app_list",
            "daemon_names": "paper_physical_ai;intern_paper_physical_ai",
            "registry_name_candidates": "intern_paper_physical_ai",
            "project_candidates": "intern_paper_reading",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 paper_physical_ai/intern_paper_reading",
            "name": "🟢 🤖 paper_physical_ai/intern_paper_reading",
            "relay_keys": "intern_paper_reading:intern_paper_physical_ai"
        },
        {
            "old_chat_id": "oc_cbb2eb93dda17b741c2f78e604e86393",
            "status": "not_in_current_app_list",
            "daemon_names": "dan_ltp",
            "registry_name_candidates": "intern_dan_ltp",
            "project_candidates": "intern_test_dan",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "intern_test_dan:intern_dan_ltp"
        },
        {
            "old_chat_id": "oc_dc8104b61c0ef7431c0996039769812f",
            "status": "not_in_current_app_list",
            "daemon_names": "test",
            "registry_name_candidates": "intern_test",
            "project_candidates": "intern_test_dan",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "intern_test_dan:intern_test"
        },
        {
            "old_chat_id": "oc_f9b9301d5a1f87143bbbf47a555aae5e",
            "status": "not_in_current_app_list",
            "daemon_names": "test_2",
            "registry_name_candidates": "intern_test_2",
            "project_candidates": "intern_test_dan",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "intern_test_dan:intern_test_2"
        },
        {
            "old_chat_id": "oc_adf240238b71cba900d6b7f626c2975c",
            "status": "not_in_current_app_list",
            "daemon_names": "hui_helper",
            "registry_name_candidates": "intern_hui_helper",
            "project_candidates": "intern_test_repo",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 hui_helper/intern_test_repo",
            "name": "🟢 🤖 hui_helper/intern_test_repo",
            "relay_keys": "intern_test_repo:intern_hui_helper",
            "new_chat_id": "oc_3c6f1cf8f166e52b31ab3cee81d97598"
        },
        {
            "old_chat_id": "oc_2f8777d67ea0562998abfa69fbb40b8e",
            "status": "not_in_current_app_list",
            "daemon_names": "ltp_monitor",
            "registry_name_candidates": "intern_ltp_monitor",
            "project_candidates": "intern_test_repo",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 ltp_monitor/intern_test_repo",
            "name": "🟢 🚀 ltp_monitor/intern_test_repo",
            "relay_keys": "intern_test_repo:intern_ltp_monitor",
            "new_chat_id": "oc_2ecdb8427fed1299bdc96c61f8e01ec9"
        },
        {
            "old_chat_id": "oc_b027d12cadd08ac7e42820396d3b9c7f",
            "status": "not_in_current_app_list",
            "daemon_names": "test_demo1;intern_test_demo1",
            "registry_name_candidates": "intern_test_demo1",
            "project_candidates": "intern_test_repo",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "intern_test_repo:intern_test_demo1"
        },
        {
            "old_chat_id": "oc_7c77e3f99e287ff4204ec20d48d882ce",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_test_demo2",
            "registry_name_candidates": "intern_test_demo2",
            "project_candidates": "intern_test_repo",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "intern_test_repo:intern_test_demo2"
        },
        {
            "old_chat_id": "oc_409e75622c592dc4ea6ae7aed7ea4072",
            "status": "not_in_current_app_list",
            "daemon_names": "tracy_helper",
            "registry_name_candidates": "intern_tracy_helper",
            "project_candidates": "intern_test_repo",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 tracy_helper/intern_test_repo",
            "name": "🟢 🤖 tracy_helper/intern_test_repo",
            "relay_keys": "intern_test_repo:intern_tracy_helper",
            "new_chat_id": "oc_9a08f6c29862c5e4fbc66b52f667ea8d"
        },
        {
            "old_chat_id": "oc_51198fcbb3cbde7eb41dbab155bf6e5c",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_ldp_explorer",
            "registry_name_candidates": "intern_ldp_explorer",
            "project_candidates": "ldp",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 ldp_explorer/ldp",
            "name": "🟢 🚀 ldp_explorer/ldp",
            "relay_keys": "ldp:intern_ldp_explorer"
        },
        {
            "old_chat_id": "oc_da18ea2ab434607165a583896db160b0",
            "status": "not_in_current_app_list",
            "daemon_names": "method_developer",
            "registry_name_candidates": "intern_method_developer",
            "project_candidates": "ldp",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 method_developer/ldp",
            "name": "🟢 🚀 method_developer/ldp",
            "relay_keys": "ldp:intern_method_developer"
        },
        {
            "old_chat_id": "oc_ade3c7ed230251ebbabe4ab5268a20d9",
            "status": "not_in_current_app_list",
            "daemon_names": "midtrain_results",
            "registry_name_candidates": "intern_midtrain_results",
            "project_candidates": "llamafactory_dan",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "llamafactory_dan:intern_midtrain_results"
        },
        {
            "old_chat_id": "oc_f9e8282963bb6acf8e72da6123ca22c0",
            "status": "not_in_current_app_list",
            "daemon_names": "midtraining",
            "registry_name_candidates": "intern_midtraining",
            "project_candidates": "llamafactory_dan",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "llamafactory_dan:intern_midtraining"
        },
        {
            "old_chat_id": "oc_a27fb3ccc251b23b24c3fda110ab6721",
            "status": "not_in_current_app_list",
            "daemon_names": "sft_debug",
            "registry_name_candidates": "intern_sft_debug",
            "project_candidates": "ltp-LLamaFactory",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 sft_debug/ltp-LLamaFactory",
            "name": "🔴 🚀 sft_debug/ltp-LLamaFactory",
            "relay_keys": "ltp-LLamaFactory:intern_sft_debug"
        },
        {
            "old_chat_id": "oc_5ef1c0e713b1889d3e73daf2263ceb80",
            "status": "not_in_current_app_list",
            "daemon_names": "test_codex",
            "registry_name_candidates": "intern_test_codex",
            "project_candidates": "ltp-LLamaFactory",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 test_codex/ltp-LLamaFactory",
            "name": "🔴 🚀 test_codex/ltp-LLamaFactory",
            "relay_keys": "ltp-LLamaFactory:intern_test_codex"
        },
        {
            "old_chat_id": "oc_67ef4c9eae47039c810eb8364ae21cb4",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_code1;code1",
            "registry_name_candidates": "intern_code1",
            "project_candidates": "ltp-megatron-lm_mm",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 code1/ltp-megatron-lm_mm",
            "name": "🔴 🚀 code1/ltp-megatron-lm_mm",
            "relay_keys": "ltp-megatron-lm_mm:intern_code1"
        },
        {
            "old_chat_id": "oc_2ac5d83a038e860b4fd7c575c48f96eb",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_code2;code2",
            "registry_name_candidates": "intern_code2",
            "project_candidates": "ltp-megatron-lm_mm",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 code2/ltp-megatron-lm_mm",
            "name": "🔴 🚀 code2/ltp-megatron-lm_mm",
            "relay_keys": "ltp-megatron-lm_mm:intern_code2"
        },
        {
            "old_chat_id": "oc_9acc8e9da9eee55f042885cae7e06e80",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_master;master",
            "registry_name_candidates": "intern_master",
            "project_candidates": "ltp-megatron-lm_mm",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 master/ltp-megatron-lm_mm",
            "name": "🔴 🤖 master/ltp-megatron-lm_mm",
            "relay_keys": "ltp-megatron-lm_mm:intern_master"
        },
        {
            "old_chat_id": "oc_36cc296dfce11c4b6f4bd583b5b44ab6",
            "status": "not_in_current_app_list",
            "daemon_names": "megatron_add_feature;intern_megatron_add_feature",
            "registry_name_candidates": "intern_megatron_add_feature",
            "project_candidates": "ltp-megatron-lm_mm",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 megatron_add_feature/ltp-megatron-lm_mm",
            "name": "🔴 🤖 megatron_add_feature/ltp-megatron-lm_mm",
            "relay_keys": "ltp-megatron-lm_mm:intern_megatron_add_feature"
        },
        {
            "old_chat_id": "oc_adc575025103ea5242b42e80dc7c528b",
            "status": "not_in_current_app_list",
            "daemon_names": "megatron_codereivew;intern_megatron_codereivew",
            "registry_name_candidates": "intern_megatron_codereivew",
            "project_candidates": "ltp-megatron-lm_mm",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 megatron_codereivew/ltp-megatron-lm_mm",
            "name": "🔴 🤖 megatron_codereivew/ltp-megatron-lm_mm",
            "relay_keys": "ltp-megatron-lm_mm:intern_megatron_codereivew"
        },
        {
            "old_chat_id": "oc_d6c083d8892f3a161640aea64dff4487",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_megatron_job;megatron_job",
            "registry_name_candidates": "intern_megatron_job",
            "project_candidates": "ltp-megatron-lm_mm",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 megatron_job/ltp-megatron-lm_mm",
            "name": "🔴 🤖 megatron_job/ltp-megatron-lm_mm",
            "relay_keys": "ltp-megatron-lm_mm:intern_megatron_job"
        },
        {
            "old_chat_id": "oc_5a519c4d82c109a1767caa1d1c7d05f3",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_xyl_2;xyl_2",
            "registry_name_candidates": "intern_xyl_2",
            "project_candidates": "ltp-megatron-lm_mm",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 xyl_2/ltp-megatron-lm_mm",
            "name": "🔴 🤖 xyl_2/ltp-megatron-lm_mm",
            "relay_keys": "ltp-megatron-lm_mm:intern_xyl_2"
        },
        {
            "old_chat_id": "oc_df714ddad5d8d50059b59e222b8dbfb9",
            "status": "not_in_current_app_list",
            "daemon_names": "ltp_sft_8b",
            "registry_name_candidates": "intern_ltp_sft_8b",
            "project_candidates": "ltp_job_agent",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "ltp_job_agent:intern_ltp_sft_8b"
        },
        {
            "old_chat_id": "oc_c51ea1d2b6464215fbbb67e8beb42e22",
            "status": "not_in_current_app_list",
            "daemon_names": "rebuttal",
            "registry_name_candidates": "intern_rebuttal",
            "project_candidates": "lupaper",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 rebuttal/lupaper",
            "name": "🟢 🤖 rebuttal/lupaper",
            "relay_keys": "lupaper:intern_rebuttal"
        },
        {
            "old_chat_id": "oc_4b98b5f5d7e76e1f8bc9a449d098d2d3",
            "status": "not_in_current_app_list",
            "daemon_names": "update_coloco",
            "registry_name_candidates": "intern_update_coloco",
            "project_candidates": "lupaper",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 update_coloco/lupaper",
            "name": "🟢 🤖 update_coloco/lupaper",
            "relay_keys": "lupaper:intern_update_coloco"
        },
        {
            "old_chat_id": "oc_1c298b2b65d64987b2618d9ee46e9640",
            "status": "not_in_current_app_list",
            "daemon_names": "code_maverl",
            "registry_name_candidates": "intern_code_maverl",
            "project_candidates": "maverl",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 code_maverl/maverl",
            "name": "🔴 🚀 code_maverl/maverl",
            "relay_keys": "maverl:intern_code_maverl"
        },
        {
            "old_chat_id": "oc_9dd05e5e62cdd953d5a2379278af039a",
            "status": "not_in_current_app_list",
            "daemon_names": "cr",
            "registry_name_candidates": "intern_cr",
            "project_candidates": "maverl",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "maverl:intern_cr"
        },
        {
            "old_chat_id": "oc_ae892b992eead882b3b38f8ef478aa73",
            "status": "not_in_current_app_list",
            "daemon_names": "cr_maverl",
            "registry_name_candidates": "intern_cr_maverl",
            "project_candidates": "maverl",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "maverl:intern_cr_maverl"
        },
        {
            "old_chat_id": "oc_843966124aa6432bb0a66cf51b5b3b18",
            "status": "not_in_current_app_list",
            "daemon_names": "maverl",
            "registry_name_candidates": "intern_maverl",
            "project_candidates": "maverl",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 maverl/maverl",
            "name": "🔴 🚀 maverl/maverl",
            "relay_keys": "maverl:intern_maverl"
        },
        {
            "old_chat_id": "oc_46af3b85dc7026e9d211bd0e90b136c6",
            "status": "not_in_current_app_list",
            "daemon_names": "mid_axisagentic",
            "registry_name_candidates": "intern_mid_axisagentic",
            "project_candidates": "midtrain_interns",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "midtrain_interns:intern_mid_axisagentic"
        },
        {
            "old_chat_id": "oc_a9a30bca55dd667adbdec17167ad68b1",
            "status": "not_in_current_app_list",
            "daemon_names": "multi_synt_claude",
            "registry_name_candidates": "intern_multi_synt_claude",
            "project_candidates": "multi-synthesis",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 multi_synt_claude/multi-synthesis",
            "name": "🟢 🤖 multi_synt_claude/multi-synthesis",
            "relay_keys": "multi-synthesis:intern_multi_synt_claude"
        },
        {
            "old_chat_id": "oc_fc18a43e844dfe18da121371de59b0ba",
            "status": "not_in_current_app_list",
            "daemon_names": "multi_synth_test_and_analyse",
            "registry_name_candidates": "intern_multi_synth_test_and_analyse",
            "project_candidates": "multi-synthesis",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "multi-synthesis:intern_multi_synth_test_and_analyse"
        },
        {
            "old_chat_id": "oc_783b50172f8db3f21c8b55de7dd3de3f",
            "status": "not_in_current_app_list",
            "daemon_names": "run_sampling",
            "registry_name_candidates": "intern_run_sampling",
            "project_candidates": "multi-synthesis",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 run_sampling/multi-synthesis",
            "name": "🟢 🤖 run_sampling/multi-synthesis",
            "relay_keys": "multi-synthesis:intern_run_sampling"
        },
        {
            "old_chat_id": "oc_743c4249a66bb43fbf2e1258572b16fc",
            "status": "not_in_current_app_list",
            "daemon_names": "vibe_synthesis",
            "registry_name_candidates": "intern_vibe_synthesis",
            "project_candidates": "multi-synthesis",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 vibe_synthesis/multi-synthesis",
            "name": "🟢 🤖 vibe_synthesis/multi-synthesis",
            "relay_keys": "multi-synthesis:intern_vibe_synthesis"
        },
        {
            "old_chat_id": "oc_92567eb6909a5f4788cc6e6efbe1d33c",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_paper_reading;paper_reading",
            "registry_name_candidates": "intern_paper_reading",
            "project_candidates": "paper_reading",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 paper_reading/paper_reading",
            "name": "🔴 🚀 paper_reading/paper_reading",
            "relay_keys": "paper_reading:intern_paper_reading"
        },
        {
            "old_chat_id": "oc_3b24bdead6f36bad07960d1bd27325a1",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_sensenova;sensenova",
            "registry_name_candidates": "intern_sensenova",
            "project_candidates": "paper_reading",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "paper_reading:intern_sensenova"
        },
        {
            "old_chat_id": "oc_97e9a2c645d3f00fe4fb7b814388b58f",
            "status": "not_in_current_app_list",
            "daemon_names": "benchmarker",
            "registry_name_candidates": "intern_benchmarker",
            "project_candidates": "sglang_benchmark",
            "type_candidates": "claude",
            "last_group_name_candidates": "🟢 🤖 benchmarker/sglang_benchmark",
            "name": "🟢 🤖 benchmarker/sglang_benchmark",
            "relay_keys": "sglang_benchmark:intern_benchmarker"
        },
        {
            "old_chat_id": "oc_f4bf4e7f7fd5f3fb61dd823367b42740",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_agentic_env_eval",
            "registry_name_candidates": "intern_agentic_env_eval",
            "project_candidates": "srl",
            "type_candidates": "codex",
            "last_group_name_candidates": "🔴 🚀 agentic_env_eval/srl",
            "name": "🔴 🚀 agentic_env_eval/srl",
            "relay_keys": "srl:intern_agentic_env_eval"
        },
        {
            "old_chat_id": "oc_584e615e7a3e118585d372d6f2d24201",
            "status": "present",
            "daemon_names": "intern_agentic_env_opt",
            "registry_name_candidates": "intern_agentic_env_opt",
            "project_candidates": "srl",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "🔴 intern_agentic_env_opt/srl",
            "relay_keys": "srl:intern_agentic_env_opt"
        },
        {
            "old_chat_id": "oc_be8cda923dc62b7f09b049ed6edb7961",
            "status": "not_in_current_app_list",
            "daemon_names": "srl_experiment_run;intern_srl_experiment_run",
            "registry_name_candidates": "intern_srl_experiment_run",
            "project_candidates": "srl",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 srl_experiment_run/srl",
            "name": "🔴 🤖 srl_experiment_run/srl",
            "relay_keys": "srl:intern_srl_experiment_run"
        },
        {
            "old_chat_id": "oc_c4cca34b8a7f74c8bc9cadc779fcaabf",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_wot_ir_monitor",
            "registry_name_candidates": "intern_wot_ir_monitor",
            "project_candidates": "srl",
            "type_candidates": "claude",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "srl:intern_wot_ir_monitor"
        },
        {
            "old_chat_id": "oc_444c0e37556952caa0b99fb9234e2c25",
            "status": "not_in_current_app_list",
            "daemon_names": "intern_wth_ir_monitor;wth_ir_monitor",
            "registry_name_candidates": "intern_wth_ir_monitor",
            "project_candidates": "srl",
            "type_candidates": "claude",
            "last_group_name_candidates": "🔴 🤖 wth_ir_monitor/srl",
            "name": "🔴 🤖 wth_ir_monitor/srl",
            "relay_keys": "srl:intern_wth_ir_monitor"
        },
        {
            "old_chat_id": "oc_a80b0859201ea44aaf38d34de7caa928",
            "status": "not_in_current_app_list",
            "daemon_names": "swe_zero",
            "registry_name_candidates": "intern_swe_zero",
            "project_candidates": "swe-zero-rerun",
            "type_candidates": "copilot",
            "last_group_name_candidates": "",
            "name": "",
            "relay_keys": "swe-zero-rerun:intern_swe_zero"
        },
        {
            "old_chat_id": "oc_e7f92653dc7e65d2e4f0a596793bbd80",
            "status": "not_in_current_app_list",
            "daemon_names": "tracking_technique;intern_tracking_technique",
            "registry_name_candidates": "intern_tracking_technique",
            "project_candidates": "tracking_info",
            "type_candidates": "codex",
            "last_group_name_candidates": "🟢 🚀 tracking_technique/tracking_info",
            "name": "🟢 🚀 tracking_technique/tracking_info",
            "relay_keys": "tracking_info:intern_tracking_technique"
        }
    ]
}

class FeishuAPIError(RuntimeError):
    pass


class FeishuAPI:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = ""
        self._token_expires = 0.0

    def _get_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires - 300:
            return self._token
        payload = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE_URL}/auth/v3/tenant_access_token/internal",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8") or "{}")
        if result.get("code") != 0:
            raise FeishuAPIError(f"tenant_access_token failed: code={result.get('code')} msg={result.get('msg')}")
        self._token = str(result.get("tenant_access_token") or "")
        self._token_expires = now + int(result.get("expire") or 7200)
        if not self._token:
            raise FeishuAPIError("tenant_access_token response missing token")
        return self._token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self._get_token()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise FeishuAPIError(f"HTTP {exc.code}: {detail}") from exc
        if result.get("code") != 0:
            raise FeishuAPIError(f"code={result.get('code')} msg={result.get('msg')}")
        data_obj = result.get("data")
        return data_obj if isinstance(data_obj, dict) else {}

    def list_chat_ids(self) -> set[str]:
        chat_ids: set[str] = set()
        page_token = ""
        while True:
            path = "/im/v1/chats?page_size=100"
            if page_token:
                path += f"&page_token={urllib_parse_quote(page_token)}"
            data = self.request("GET", path)
            for item in data.get("items") or []:
                chat_id = str(item.get("chat_id") or "")
                if chat_id:
                    chat_ids.add(chat_id)
            if not data.get("has_more"):
                return chat_ids
            page_token = str(data.get("page_token") or "")
            if not page_token:
                return chat_ids

    def mobile_to_open_id(self, mobile: str) -> str:
        data = self.request(
            "POST",
            "/contact/v3/users/batch_get_id?user_id_type=open_id",
            {"mobiles": [mobile]},
        )
        user_list = data.get("user_list") or []
        if user_list and user_list[0].get("user_id"):
            return str(user_list[0]["user_id"])
        raise FeishuAPIError(f"mobile {mobile!r} not found in this tenant")

    def create_chat(self, name: str, owner_open_id: str) -> str:
        body: dict[str, Any] = {
            "name": name,
            "description": f"Intern agent: {name}",
            "chat_type": "private",
            "user_id_list": [owner_open_id],
        }
        data = self.request("POST", "/im/v1/chats?user_id_type=open_id", body)
        chat_id = str(data.get("chat_id") or "")
        if not chat_id:
            raise FeishuAPIError(f"create_chat returned no chat_id for {name!r}")
        return chat_id

    def add_chat_managers(self, chat_id: str, owner_open_id: str) -> None:
        self.request(
            "POST",
            f"/im/v1/chats/{chat_id}/managers/add_managers?member_id_type=open_id",
            {"manager_ids": [owner_open_id]},
        )

    def send_message(self, chat_id: str, text: str) -> None:
        content_lines = [[{"tag": "text", "text": line}] for line in text.splitlines() or [text]]
        content = json.dumps({"zh_cn": {"content": content_lines}}, ensure_ascii=False)
        self.request(
            "POST",
            "/im/v1/messages?receive_id_type=chat_id",
            {"receive_id": chat_id, "msg_type": "post", "content": content},
        )


def urllib_parse_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def as_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("rows", "items", "results", "created"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def is_green_row(row: dict[str, Any]) -> bool:
    text = first_text(row, ("name", "current_name", "last_group_name", "last_group_name_candidates", "registry_name_candidates"))
    if "🟢" in text:
        return True
    if "🔴" in text or "⚪" in text:
        return False
    return str(row.get("online") or row.get("is_online") or "").lower() in {"1", "true", "yes", "green"}


def normalize_allowlist_interns(row: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("intern_name", "intern", "daemon_names", "daemon_intern_names", "registry_name_candidates"):
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            parts = value
        else:
            parts = re.split(r"[;,]", str(value))
        for part in parts:
            name = str(part).strip()
            if name:
                names.update(intern_name_aliases(name))
    return names


def intern_name_aliases(intern_name: str) -> set[str]:
    name = intern_name.strip()
    if not name:
        return set()
    aliases = {name}
    if name.startswith("intern_"):
        aliases.add(name[len("intern_"):])
    else:
        aliases.add(f"intern_{name}")
    return aliases


def is_canonical_intern_name(intern_name: str) -> bool:
    return intern_name.startswith("intern_")


def build_incident_allowlist(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    allowlist: dict[str, dict[str, Any]] = {}
    for row in as_rows(data):
        old_chat_id = first_text(row, ("old_chat_id", "chat_id"))
        if not old_chat_id:
            continue
        green = is_green_row(row)
        allowlist[old_chat_id] = {
            "old_chat_id": old_chat_id,
            "green": green,
            "name": first_text(row, ("name", "current_name", "last_group_name", "last_group_name_candidates", "registry_name_candidates")),
            "new_chat_id": first_text(row, ("new_chat_id", "restored_chat_id")),
            "project": first_text(row, ("project", "project_candidates")),
            "type": first_text(row, ("type", "type_candidates")),
            "intern_names": sorted(normalize_allowlist_interns(row)),
        }
    return allowlist


def load_incident_allowlist(path: Path) -> dict[str, dict[str, Any]]:
    return build_incident_allowlist(read_json(path))


def load_default_incident_allowlist() -> tuple[dict[str, dict[str, Any]], str]:
    return build_incident_allowlist(DEFAULT_INCIDENT_REPORT), DEFAULT_INCIDENT_REPORT_NAME


def incident_by_new_chat_id(allowlist: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in allowlist.values():
        new_chat_id = str(item.get("new_chat_id") or "").strip()
        if new_chat_id:
            result[new_chat_id] = item
    return result


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_feishu_credentials(root: Path) -> tuple[str, str]:
    path = daemon_policy_path(root)
    policy = read_json(path)
    feishu = policy.get("feishu") if isinstance(policy.get("feishu"), dict) else {}
    app_id = str(feishu.get("app_id") or "").strip()
    app_secret = str(feishu.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise ValueError(f"{path} missing feishu.app_id/app_secret")
    return app_id, app_secret


def load_owner_open_id(root: Path, api: FeishuAPI) -> str:
    owner_path = daemon_owner_path(root)
    owner = read_json(owner_path)
    open_id = str(owner.get("owner_open_id") or owner.get("open_id") or "").strip()
    if open_id:
        return open_id
    mobile = str(owner.get("mobile") or "").strip()
    if not mobile:
        raise ValueError(f"{owner_path} needs owner_open_id/open_id or mobile")
    open_id = api.mobile_to_open_id(mobile)
    owner["owner_open_id"] = open_id
    owner["open_id"] = open_id
    write_json_atomic(owner_path, owner)
    return open_id


def load_owner_config(root: Path) -> dict[str, Any]:
    path = daemon_owner_path(root)
    return read_json(path)


def safe_file_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._") or "default"


def load_sessions(root: Path) -> dict[str, Any]:
    path = root / ".intern_sessions.json"
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data


def session_entry(sessions: dict[str, Any], intern_name: str, project: str) -> dict[str, Any]:
    direct = sessions.get(f"{project}:{intern_name}") if project else sessions.get(intern_name)
    if isinstance(direct, dict):
        return direct
    matches = []
    for key, value in sessions.items():
        if not isinstance(value, dict):
            continue
        if value.get("intern_name") == intern_name or key == intern_name or key.endswith(f":{intern_name}"):
            if project and value.get("project") not in ("", project) and not key.startswith(f"{project}:"):
                continue
            matches.append(value)
    return matches[0] if len(matches) == 1 else {}


def infer_project(data: dict[str, Any], sessions: dict[str, Any], intern_name: str) -> str:
    project = str(data.get("project") or "").strip()
    if project:
        return project
    entry = session_entry(sessions, intern_name, "")
    project = str(entry.get("project") or "").strip() if entry else ""
    return project or "axis_intern_agents"


def infer_type(data: dict[str, Any], sessions: dict[str, Any], intern_name: str, project: str) -> str:
    intern_type = str(data.get("type") or "").strip()
    if intern_type:
        return intern_type
    entry = session_entry(sessions, intern_name, project)
    intern_type = str(entry.get("type") or "").strip() if entry else ""
    return intern_type or "copilot"


def build_group_name(intern_name: str, project: str, intern_type: str) -> str:
    stripped = intern_name[len("intern_"):] if intern_name.startswith("intern_") else intern_name
    badge = TYPE_EMOJI.get(intern_type or "copilot", "")
    return f"🟢 {badge}{stripped}/{project}"


def resolve_tmux_session_name(sessions: dict[str, Any], intern_name: str, project: str) -> str:
    entry = session_entry(sessions, intern_name, project)
    if not entry:
        return ""
    explicit = str(entry.get("tmux_session") or "").strip()
    if explicit:
        return explicit
    workspace_id = str(entry.get("workspace_id") or "").strip()
    intern_dir = str(entry.get("intern_dir") or "").strip()
    if workspace_id and intern_dir:
        return scoped_tmux_session_name(
            intern_name,
            project=str(entry.get("project") or project or ""),
            workspace_id=workspace_id,
            intern_dir=intern_dir,
        )
    return ""


def tmux_session_active(session_name: str) -> bool:
    if not session_name:
        return False
    has_session = subprocess.run(
        ["tmux", "has-session", "-t", f"={session_name}"],
        capture_output=True,
    )
    return has_session.returncode == 0


def is_active_on_this_machine(
    intern_name: str,
    intern_type: str,
    sessions: dict[str, Any],
    project: str,
) -> bool:
    if intern_type in {"claude", "codex"}:
        return tmux_session_active(resolve_tmux_session_name(sessions, intern_name, project))
    # Copilot activity is owned by VS Code windows and is not safely inferable
    # from a standalone script. Keep the default conservative.
    return False


def registry_files(root: Path) -> list[Path]:
    reg_dir = root / ".feishu_registry"
    if not reg_dir.is_dir():
        raise FileNotFoundError(f"registry dir not found: {reg_dir}")
    return sorted(p for p in reg_dir.glob("*.json") if not p.name.startswith("_"))


def backup_registry(root: Path) -> Path:
    src = root / ".feishu_registry"
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = root / f".feishu_registry.backup.{ts}"
    shutil.copytree(src, dst)
    return dst


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def restart_daemon_direct(root: Path, daemon_addr_path: Path) -> dict[str, Any]:
    info = read_json(daemon_addr_path)
    pid = int(info.get("pid") or 0)
    bundle_dir = str(info.get("bundle_dir") or "").strip()
    script = Path(bundle_dir) / "scripts" / "daemon" / "feishu_daemon.py"
    if not pid or not script.is_file():
        return {"attempted": True, "ok": False, "error": f"cannot locate running daemon from {daemon_addr_path}"}
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        pass
    for _ in range(40):
        if not process_exists(pid):
            break
        time.sleep(0.25)
    if process_exists(pid):
        os.kill(pid, 9)
        for _ in range(20):
            if not process_exists(pid):
                break
            time.sleep(0.25)
    log_dir = system_log_dir(root, "daemon", bundle_dir=bundle_dir, component_version="unknown")
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / "feishu_daemon.repair_restart.out"
    env = os.environ.copy()
    env["WORK_AGENTS_ROOT"] = str(root)
    with out_path.open("ab") as out:
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(root),
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.time() + 45
    new_info: dict[str, Any] = {}
    while time.time() < deadline:
        if proc.poll() is not None:
            return {
                "attempted": True,
                "ok": False,
                "method": "direct",
                "error": f"daemon exited during restart with code {proc.returncode}",
                "stdout": str(out_path),
            }
        try:
            new_info = read_json(daemon_addr_path)
        except Exception:
            time.sleep(0.5)
            continue
        if int(new_info.get("pid") or 0) != pid and new_info.get("http_port"):
            return {
                "attempted": True,
                "ok": True,
                "method": "direct",
                "old_pid": pid,
                "new_pid": int(new_info.get("pid") or 0),
                "command": f"{sys.executable} {script}",
                "stdout": str(out_path),
            }
        time.sleep(0.5)
    return {"attempted": True, "ok": False, "method": "direct", "error": "daemon restart timed out", "stdout": str(out_path)}


def restart_daemon(root: Path, internctl: Path | None, daemon_addr_path: Path) -> dict[str, Any]:
    candidates = []
    if internctl:
        candidates.append(internctl)
    candidates.append(Path(__file__).resolve().parents[1] / "internctl.py")
    candidates.append(root / "axis_intern_agents" / "intern-cli" / "internctl.py")
    candidates.append(root / "axis_intern_agents" / "vscode-extension" / "bundled-cli" / "internctl.py")
    failures = []
    for candidate in candidates:
        if candidate.exists():
            env = os.environ.copy()
            env["WORK_AGENTS_ROOT"] = str(root)
            proc = subprocess.run(
                [sys.executable, str(candidate), "daemon", "restart"],
                text=True,
                capture_output=True,
                timeout=120,
                env=env,
            )
            result = {
                "attempted": True,
                "ok": proc.returncode == 0,
                "method": "internctl",
                "command": f"{sys.executable} {candidate} daemon restart",
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
            }
            if proc.returncode == 0:
                return result
            failures.append(result)
    direct = restart_daemon_direct(root, daemon_addr_path)
    if failures:
        direct["internctl_failures"] = failures
    return direct


def load_daemon_addr(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if not data.get("http_port"):
        raise ValueError(f"{path} missing http_port")
    return data


def post_json(url: str, body: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def get_json(url: str, timeout: int = 10) -> Any:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "null")


def load_live_daemon_groups(daemon_addr_path: Path) -> dict[str, Any]:
    try:
        daemon_addr = load_daemon_addr(daemon_addr_path)
        base = f"http://127.0.0.1:{int(daemon_addr['http_port'])}"
        data = get_json(f"{base}/api/group/list", timeout=5)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "by_key": {}, "by_name": {}}
    if not isinstance(data, list):
        return {"ok": False, "error": "/api/group/list did not return a list", "by_key": {}, "by_name": {}}
    by_key: dict[tuple[str, str], str] = {}
    by_name: dict[str, list[str]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("intern_name") or "").strip()
        project = str(item.get("project") or "").strip()
        chat_id = str(item.get("chat_id") or "").strip()
        if not name or not chat_id:
            continue
        by_key[(name, project)] = chat_id
        by_name.setdefault(name, []).append(chat_id)
    return {"ok": True, "error": "", "by_key": by_key, "by_name": by_name}


def live_daemon_chat_id(live_groups: dict[str, Any], intern_name: str, project: str) -> str:
    by_key = live_groups.get("by_key") or {}
    chat_id = by_key.get((intern_name, project))
    if chat_id:
        return chat_id
    chat_id = by_key.get((intern_name, ""))
    if chat_id:
        return chat_id
    by_name = live_groups.get("by_name") or {}
    values = sorted(set(by_name.get(intern_name) or []))
    return values[0] if len(values) == 1 else ""


def sync_relay_register(root: Path, daemon_addr_path: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {"attempted": False, "ok": True, "registered": 0}
    try:
        import websockets.sync.client as ws_sync
    except Exception as exc:
        return {"attempted": True, "ok": False, "error": f"websockets.sync.client unavailable: {exc}"}

    owner = load_owner_config(root)
    relay_url = str(owner.get("relay_url") or "").strip()
    relay_token = str(owner.get("relay_token") or "").strip()
    if not relay_url or not relay_token:
        return {"attempted": True, "ok": False, "error": "_owner.json missing relay_url/relay_token"}
    daemon_addr = load_daemon_addr(daemon_addr_path)
    daemon_machine_id = str(daemon_addr.get("instance_id") or "").strip()
    if not daemon_machine_id:
        return {"attempted": True, "ok": False, "error": f"{daemon_addr_path} missing instance_id"}
    # Do not authenticate as the real daemon machine_id. A second connection
    # using the same machine_id can displace the live daemon connection. Relay's
    # register_interns path refreshes chat_id for an already-owned intern without
    # stealing ownership when the registering machine_id is different.
    machine_id = f"{daemon_machine_id}:repair"
    payload = [
        {
            "name": item["intern"],
            "project": item["project"],
            "type": item["type"],
            "chat_id": item["new_chat_id"],
        }
        for item in entries
    ]
    with ws_sync.connect(relay_url) as ws:
        ws.send(json.dumps({
            "type": "auth",
            "token": relay_token,
            "machine_id": machine_id,
            "owner_mobile": owner.get("mobile", ""),
            "owner_open_id": owner.get("owner_open_id") or owner.get("open_id") or "",
            "ip": daemon_addr.get("ip", ""),
            "ssh_port": daemon_addr.get("ssh_port", 22),
            "script_hash": "repair_feishu_daemon_registry",
            "capabilities": [],
        }))
        resp = json.loads(ws.recv(timeout=10))
        if resp.get("type") != "auth_result" or not resp.get("ok"):
            return {"attempted": True, "ok": False, "error": f"relay auth failed: {resp}"}
        ws.send(json.dumps({"type": "register_interns", "interns": payload}))
    return {"attempted": True, "ok": True, "registered": len(payload), "machine_id": machine_id, "target_machine_id": daemon_machine_id}


def refresh_daemon_registry(daemon_addr_path: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {"attempted": False, "ok": True, "refreshed": 0}
    daemon_addr = load_daemon_addr(daemon_addr_path)
    base = f"http://127.0.0.1:{int(daemon_addr['http_port'])}"
    results = []
    ok = True
    for item in entries:
        body = {"intern_name": item["intern"], "project": item["project"], "type": item["type"]}
        try:
            resp = post_json(f"{base}/api/group/create", body, timeout=70)
            item_ok = resp.get("chat_id") == item["new_chat_id"]
            ok = ok and item_ok
            results.append({"intern": item["intern"], "project": item["project"], "ok": item_ok, "response": resp})
        except Exception as exc:
            ok = False
            results.append({"intern": item["intern"], "project": item["project"], "ok": False, "error": str(exc)})
    return {"attempted": True, "ok": ok, "refreshed": sum(1 for r in results if r.get("ok")), "results": results}


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    allowlist, incident_report = load_default_incident_allowlist()
    allowlist_by_new = incident_by_new_chat_id(allowlist)
    if not allowlist:
        raise ValueError("incident report contains no allowlisted chat ids")
    app_id, app_secret = read_feishu_credentials(root)
    api = FeishuAPI(app_id, app_secret)
    owner_open_id = load_owner_open_id(root, api)
    visible_chat_ids = api.list_chat_ids()
    sessions = load_sessions(root)
    files = registry_files(root)
    live_groups = load_live_daemon_groups(Path(args.daemon_addr).expanduser().resolve())
    backup_path = None if args.dry_run else backup_registry(root)
    old_to_new: dict[str, str] = {}
    results = []
    refresh_entries = []

    for path in files:
        data = read_json(path)
        intern_name = str(data.get("internName") or path.stem).strip()
        old_chat_id = str(data.get("chatId") or "").strip()
        if not intern_name or not old_chat_id:
            results.append({"file": str(path), "action": "skip", "reason": "missing internName/chatId"})
            continue
        if not is_canonical_intern_name(intern_name):
            results.append({
                "file": str(path),
                "intern": intern_name,
                "old_chat_id": old_chat_id,
                "action": "skip",
                "reason": "non_canonical_registry_alias",
            })
            continue
        incident = allowlist.get(old_chat_id)
        already_new = False
        if not incident:
            incident = allowlist_by_new.get(old_chat_id)
            already_new = bool(incident)
        if not incident:
            results.append({"file": str(path), "intern": intern_name, "old_chat_id": old_chat_id, "action": "skip", "reason": "not_in_incident_report"})
            continue
        if not incident.get("green"):
            results.append({"file": str(path), "intern": intern_name, "old_chat_id": old_chat_id, "action": "skip", "reason": "not_green_in_incident_report"})
            continue
        expected_names = set(incident.get("intern_names") or [])
        if expected_names and not (intern_name_aliases(intern_name) & expected_names):
            results.append({
                "file": str(path),
                "intern": intern_name,
                "old_chat_id": old_chat_id,
                "action": "skip",
                "reason": f"intern_not_in_incident_names:{','.join(sorted(expected_names))}",
            })
            continue
        project = str(incident.get("project") or "").strip() or infer_project(data, sessions, intern_name)
        intern_type = str(incident.get("type") or "").strip() or infer_type(data, sessions, intern_name, project)
        if args.only_active and not is_active_on_this_machine(intern_name, intern_type, sessions, project):
            results.append({
                "file": str(path),
                "intern": intern_name,
                "old_chat_id": old_chat_id,
                "project": project,
                "type": intern_type,
                "action": "skip",
                "reason": "not_active_on_this_machine",
            })
            continue
        if already_new:
            live_chat_id = live_daemon_chat_id(live_groups, intern_name, project)
            if live_groups.get("ok") and live_chat_id == old_chat_id:
                results.append({
                    "file": str(path),
                    "intern": intern_name,
                    "project": project,
                    "type": intern_type,
                    "old_chat_id": str(incident.get("old_chat_id") or ""),
                    "new_chat_id": old_chat_id,
                    "group_name": str(incident.get("name") or "").strip() or build_group_name(intern_name, project, intern_type),
                    "action": "already_repaired_live",
                    "source": "local_registry_and_live_daemon_already_new",
                })
                continue
            item = {
                "file": str(path),
                "intern": intern_name,
                "project": project,
                "type": intern_type,
                "old_chat_id": str(incident.get("old_chat_id") or ""),
                "new_chat_id": old_chat_id,
                "group_name": str(incident.get("name") or "").strip() or build_group_name(intern_name, project, intern_type),
                "action": "refresh_existing_new",
                "source": "local_registry_already_new",
                "live_daemon_chat_id": live_chat_id,
                "reason": "daemon_live_unknown" if not live_groups.get("ok") else "live_daemon_not_updated",
            }
            results.append(item)
            refresh_entries.append(item)
            continue
        if old_chat_id in visible_chat_ids:
            results.append({"file": str(path), "intern": intern_name, "old_chat_id": old_chat_id, "action": "keep"})
            continue
        group_name = str(incident.get("name") or "").strip() or build_group_name(intern_name, project, intern_type)
        if "🟢" not in group_name:
            group_name = build_group_name(intern_name, project, intern_type)
        new_chat_id = old_to_new.get(old_chat_id, "")
        created = False
        incident_new_chat_id = str(incident.get("new_chat_id") or "").strip()
        if not new_chat_id and incident_new_chat_id:
            if incident_new_chat_id not in visible_chat_ids:
                results.append({
                    "file": str(path),
                    "intern": intern_name,
                    "old_chat_id": old_chat_id,
                    "new_chat_id": incident_new_chat_id,
                    "action": "error",
                    "reason": "incident_new_chat_id_not_visible",
                })
                continue
            new_chat_id = incident_new_chat_id
            old_to_new[old_chat_id] = new_chat_id
        if not new_chat_id and not args.dry_run:
            new_chat_id = api.create_chat(group_name, owner_open_id)
            created = True
            try:
                api.add_chat_managers(new_chat_id, owner_open_id)
            except FeishuAPIError as exc:
                results.append({
                    "file": str(path),
                    "intern": intern_name,
                    "old_chat_id": old_chat_id,
                    "new_chat_id": new_chat_id,
                    "action": "warn",
                    "warning": f"add manager failed: {exc}",
                })
            try:
                api.send_message(new_chat_id, f"Intern group restored for {intern_name}/{project}.")
            except FeishuAPIError:
                pass
            old_to_new[old_chat_id] = new_chat_id
        elif args.dry_run:
            new_chat_id = incident_new_chat_id or "<dry-run-create>"

        if not args.dry_run:
            data["chatId"] = new_chat_id
            # Current daemon chat registration stores "name -> chat_id".
            # Project scope remains in the incident report and daemon session
            # metadata, not in this chat mapping file.
            data.pop("project", None)
            write_json_atomic(path, data)
        results.append({
            "file": str(path),
            "intern": intern_name,
            "project": project,
            "type": intern_type,
            "old_chat_id": old_chat_id,
            "new_chat_id": new_chat_id,
            "group_name": group_name,
            "action": "recreate",
            "created": created,
            "source": "incident_new_chat_id" if incident_new_chat_id and not created else "created_by_script",
        })

    changed = [item for item in results if item.get("action") == "recreate"]
    sync_entries = changed + refresh_entries
    errors = [item for item in results if item.get("action") == "error"]
    daemon_restart = None
    if sync_entries and args.restart_daemon and not args.dry_run:
        daemon_restart = restart_daemon(
            root,
            Path(args.internctl).expanduser().resolve() if args.internctl else None,
            Path(args.daemon_addr).expanduser().resolve(),
        )
    return {
        "ok": not errors and (not daemon_restart or bool(daemon_restart.get("ok"))),
        "root": str(root),
        "dry_run": bool(args.dry_run),
        "incident_report": incident_report,
        "allowlisted_chat_ids": len(allowlist),
        "registry_files": len(files),
        "visible_chat_ids": len(visible_chat_ids),
        "daemon_live_check": {
            "ok": bool(live_groups.get("ok")),
            "error": str(live_groups.get("error") or ""),
        },
        "backup": str(backup_path) if backup_path else "",
        "changed": len(changed),
        "refresh_existing_new": len(refresh_entries),
        "already_repaired_live": sum(1 for item in results if item.get("action") == "already_repaired_live"),
        "kept": sum(1 for item in results if item.get("action") == "keep"),
        "skipped": sum(1 for item in results if item.get("action") == "skip"),
        "errors": len(errors),
        "results": results,
        "daemon_restart": daemon_restart,
        "restart_instruction": (
            "The script restarts the local daemon after repaired entries. It does not restart individual intern sessions."
        ),
    }


def require_work_agents_cwd() -> None:
    cwd = Path.cwd().resolve()
    if cwd != WORK_AGENTS_ROOT:
        raise RuntimeError(
            f"Please run this script from {WORK_AGENTS_ROOT}: "
            f"cd {WORK_AGENTS_ROOT} && python3 /path/to/repair_feishu_daemon_registry.py"
        )


def print_human_summary(report: dict[str, Any]) -> None:
    mode = "DRY RUN" if report.get("dry_run") else "APPLIED"
    print(f"Feishu daemon registry repair [{mode}]")
    print(f"  ok: {report.get('ok')}")
    print(f"  root: {report.get('root')}")
    print(f"  allowlist: {report.get('incident_report')}")
    print(f"  registry files scanned: {report.get('registry_files')}")
    print(f"  registry entries to update: {report.get('changed')}")
    print(f"  already repaired entries to refresh through daemon restart: {report.get('refresh_existing_new')}")
    print(f"  already repaired and live in daemon: {report.get('already_repaired_live')}")
    live = report.get("daemon_live_check") or {}
    if live and not live.get("ok"):
        print(f"  daemon live check: failed ({live.get('error')})")
    print(f"  skipped entries: {report.get('skipped')}")
    print(f"  errors: {report.get('errors')}")
    if report.get("backup"):
        print(f"  backup: {report.get('backup')}")
    restart = report.get("daemon_restart")
    if restart:
        print(f"  daemon restart: {'ok' if restart.get('ok') else 'failed'}")
    actions = [
        item for item in report.get("results", [])
        if item.get("action") in {"recreate", "refresh_existing_new", "error"}
    ]
    if actions:
        print("  affected entries:")
        for item in actions[:50]:
            action = item.get("action")
            intern = item.get("intern", "")
            project = item.get("project", "")
            old_chat_id = item.get("old_chat_id", "")
            new_chat_id = item.get("new_chat_id", "")
            reason = item.get("reason", "")
            suffix = f" -> {new_chat_id}" if new_chat_id else ""
            if reason:
                suffix += f" ({reason})"
            print(f"    - {action}: {intern}/{project} {old_chat_id}{suffix}")
        if len(actions) > 50:
            print(f"    ... {len(actions) - 50} more")
    if report.get("dry_run"):
        print("No files were modified.")


def confirmation_required(report: dict[str, Any]) -> bool:
    return bool(report.get("changed") or report.get("refresh_existing_new"))


def confirm_or_abort() -> bool:
    print()
    print("Type YES to apply these changes and restart the local daemon.")
    try:
        answer = input("> ").strip()
    except EOFError:
        return False
    return answer == "YES"


def build_args(dry_run: bool) -> argparse.Namespace:
    return argparse.Namespace(
        root=str(WORK_AGENTS_ROOT),
        dry_run=dry_run,
        only_active=True,
        daemon_addr=os.environ.get("FEISHU_DAEMON_ADDR_FILE", "/tmp/feishu_daemon.json"),
        restart_daemon=True,
        internctl="",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair local daemon Feishu registry after the 2026-05-31 group incident.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without modifying files.")
    cli_args = parser.parse_args(argv)
    try:
        require_work_agents_cwd()
        if cli_args.dry_run:
            report = run(build_args(dry_run=True))
            print_human_summary(report)
            return 0 if report.get("ok") else 1
        preview = run(build_args(dry_run=True))
        print_human_summary(preview)
        if not preview.get("ok"):
            return 1
        if not confirmation_required(preview):
            print("Nothing to apply.")
            return 0
        if not confirm_or_abort():
            print("Aborted. No files were modified.")
            return 1
        report = run(build_args(dry_run=False))
    except Exception as exc:
        report = {"ok": False, "error": str(exc)}
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print_human_summary(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
