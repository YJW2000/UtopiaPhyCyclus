# -*- coding: utf-8 -*-
"""
Pi-Agent 领域技能扩展库 (Agent Skills Package)
"""

from .base_skill import BaseDiagramSkill
from .intent_understanding_skill import IntentUnderstandingSkill
from .physics_mechanics_skill import PhysicsMechanicsSkill
from .electromagnetism_circuits_skill import ElectromagnetismCircuitsSkill
from .optics_geometry_skill import OpticsGeometrySkill
from .physics_diagram_auditor_skill import PhysicsDiagramAuditorSkill
from .tikz_error_doctor_skill import TikzErrorDoctorSkill
from .skill_registry import SkillRegistry, get_default_skill_registry
from .plugin_loader import discover_plugin_manifests, load_plugins

__all__ = [
    "BaseDiagramSkill",
    "IntentUnderstandingSkill",
    "PhysicsMechanicsSkill",
    "ElectromagnetismCircuitsSkill",
    "OpticsGeometrySkill",
    "PhysicsDiagramAuditorSkill",
    "TikzErrorDoctorSkill",
    "SkillRegistry",
    "get_default_skill_registry",
    "discover_plugin_manifests",
    "load_plugins",
]
