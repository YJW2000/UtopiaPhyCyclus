# -*- coding: utf-8 -*-
"""
Pi-Agent 外部技能插件加载器 (Plugin Loader)

目录约定：
    services/agent_skills/plugins/<plugin_name>/
        manifest.json      # {"name": str, "version": str, "entry": "skill.py",
                           #  "class": "ClassName", "enabled": true, "description": str}
        skill.py           # 定义继承 BaseDiagramSkill 的技能类

设计目标：
1. 允许把开源/外部技能以插件包形式装入而不改动框架代码；
2. 单个插件加载失败只记录日志，绝不阻塞主流程；
3. 不在 import 期执行网络/编译等副作用操作（插件类实例化时也必须轻量）。

环境变量：
    PI_AGENT_DISABLE_PLUGINS=1   # 关闭插件加载
    PI_AGENT_PLUGIN_DIR=<path>   # 自定义插件目录（默认为本文件同级 plugins/）
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base_skill import BaseDiagramSkill

logger = logging.getLogger(__name__)


def plugin_root() -> Path:
    custom = os.getenv("PI_AGENT_PLUGIN_DIR", "").strip()
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent / "plugins"


def discover_plugin_manifests(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """扫描插件目录，返回 [(manifest_path, manifest_dict)]（按目录名排序）。"""
    base = root or plugin_root()
    results: List[Dict[str, Any]] = []
    if not base.exists():
        return results
    for manifest_path in sorted(base.glob("*/manifest.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            data["_manifest_path"] = str(manifest_path)
            data["_plugin_name"] = data.get("name") or manifest_path.parent.name
            results.append(data)
        except Exception as exc:
            logger.warning("skip invalid plugin manifest %s: %s", manifest_path, exc)
    return results


def _instantiate_plugin(manifest: Dict[str, Any]) -> Optional[BaseDiagramSkill]:
    """优先按包内模块导入，使插件可以使用 from ..base_skill 相对导入。"""
    import importlib
    import importlib.util
    import sys
    import types

    manifest_path = Path(manifest["_manifest_path"])
    entry = manifest.get("entry") or "skill.py"
    entry_stem = Path(entry).stem
    class_name = manifest.get("class")
    plugin_dir = manifest_path.parent.name
    if not class_name:
        raise ValueError("manifest 缺少 class 字段")
    module_path = manifest_path.parent / entry
    if not module_path.exists():
        raise FileNotFoundError(f"插件入口不存在: {module_path}")

    # 方式 1：作为 services.agent_skills.plugins.<dir>.<entry> 包模块导入（支持相对导入）
    package_name = f"{__package__}.plugins"
    plugin_pkg_name = f"{package_name}.{plugin_dir}"
    try:
        if package_name not in sys.modules:
            plugins_pkg = types.ModuleType(package_name)
            plugins_pkg.__path__ = [str(manifest_path.parent.parent)]  # type: ignore[attr-defined]
            sys.modules[package_name] = plugins_pkg
        if plugin_pkg_name not in sys.modules:
            plugin_pkg = types.ModuleType(plugin_pkg_name)
            plugin_pkg.__path__ = [str(manifest_path.parent)]  # type: ignore[attr-defined]
            plugin_pkg.__package__ = plugin_pkg_name
            sys.modules[plugin_pkg_name] = plugin_pkg
        module = importlib.import_module(f"{plugin_pkg_name}.{entry_stem}")
        cls = getattr(module, class_name)
    except Exception:
        # 方式 2：回退到按文件路径加载（适用于没有 __init__.py 的自定义插件目录）
        module_name = f"pi_agent_plugin_{plugin_dir}".replace("-", "_").replace(".", "_")
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载插件模块: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls = getattr(module, class_name)
    instance = cls()
    if not isinstance(instance, BaseDiagramSkill):
        raise TypeError(f"插件类 {class_name} 未继承 BaseDiagramSkill")
    return instance


def load_plugins(registry: Any) -> List[str]:
    """扫描并注册所有启用的插件技能，返回成功注册的 skill_id 列表。"""
    if os.getenv("PI_AGENT_DISABLE_PLUGINS", "0").strip() == "1":
        return []
    loaded: List[str] = []
    for manifest in discover_plugin_manifests():
        name = manifest.get("_plugin_name") or "?"
        try:
            if manifest.get("enabled", True) is False:
                continue
            skill = _instantiate_plugin(manifest)
            if skill is None:
                continue
            registry.register(skill)
            loaded.append(skill.skill_id)
            logger.info("plugin skill loaded: %s (%s)", skill.skill_id, name)
        except Exception as exc:
            logger.warning("plugin %s load failed: %s", name, exc)
    return loaded


__all__ = ["plugin_root", "discover_plugin_manifests", "load_plugins"]
