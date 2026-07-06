bl_info = {
    "name": "NavTools",
    "author": "Dave Wilson",
    "version": (1, 0, 2),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > NavTools > NavTools",
    "description": "This tool helps artists who prefer a more Maya-like method of navigating a 3D environment. One click gives you Maya-style controls, and it is just as easy to switch back. Built with assistance from AI.",
    "category": "3D View",
}

"""
NavTools by Dave Wilson.

This tool has been created to help artists who prefer a more Maya-like method
of navigating a 3D environment. One click gives you Maya-style controls, and it
is just as easy to switch back.

AI disclosure: AI assistance was used during the construction, iteration and
refinement of this tool. Final design decisions, testing direction and release
ownership remain with Dave Wilson.

Quick install and use:
1. In Blender, go to Edit > Preferences > Add-ons > Install.
2. Select this Python file and enable the add-on.
3. Open the 3D View sidebar with N, then choose the NavTools tab.
4. Tick Enable NavTools to use the Maya-style navigation and transform layout.
5. Use Restore Blender Defaults before disabling or uninstalling if you want to
   return to Blender's native controls.

Known limitations:
- Shift + R uses Blender's native increment scale behaviour. NavTools shows a
  fixed 10% guide/counter, but the final snapping behaviour is still handled by
  Blender and can vary slightly by Blender version or keymap setup.
- The Unreal-style Navigation option uses Blender's built-in walk/fly navigation
  and may feel different depending on Blender's own navigation settings.
"""

import bpy
import time
import textwrap
from bpy.types import Operator, Panel, Menu, PropertyGroup
from bpy.props import BoolProperty, EnumProperty, PointerProperty, FloatProperty, IntProperty

try:
    import blf
except Exception:
    blf = None

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except Exception:
    gpu = None
    batch_for_shader = None

try:
    from mathutils import Vector
except Exception:
    Vector = None

try:
    from bpy_extras.view3d_utils import location_3d_to_region_2d
except Exception:
    location_3d_to_region_2d = None

try:
    import bmesh
except Exception:
    bmesh = None

addon_keymaps = []
_original_input_settings = {}
_disabled_keymap_conflicts = []
_hint_draw_handler = None
_scale_readout_state = {
    "active": False,
    "signature": None,
    "baseline_extents": None,
    "baseline_scales": None,
    "axis": "UNIFORM",
    "increment_snap": False,
    "started_at": 0.0,
    "last_extents": None,
    "last_change_at": 0.0,
}

_hover_tool_state = {
    "running": False,
    "mouse_x": -10000,
    "mouse_y": -10000,
    "mouse_window_x": -10000,
    "mouse_window_y": -10000,
    "last_move_at": 0.0,
}


# -----------------------------------------------------------------------------
# Settings helpers
# -----------------------------------------------------------------------------

def get_settings(context=None):
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm and hasattr(wm, "navtools_settings"):
        return wm.navtools_settings
    return None


def _input_prefs():
    try:
        return bpy.context.preferences.inputs
    except Exception:
        return None


def _set_input_pref(attr, value):
    prefs = _input_prefs()
    if prefs and hasattr(prefs, attr):
        if attr not in _original_input_settings:
            try:
                _original_input_settings[attr] = getattr(prefs, attr)
            except Exception:
                pass
        try:
            setattr(prefs, attr, value)
            return True
        except Exception:
            return False
    return False


def _restore_input_prefs():
    prefs = _input_prefs()
    if not prefs:
        return
    for attr, value in list(_original_input_settings.items()):
        if hasattr(prefs, attr):
            try:
                setattr(prefs, attr, value)
            except Exception:
                pass
    _original_input_settings.clear()


def _space_is_view3d(context):
    return bool(getattr(context, "area", None) and context.area.type == "VIEW_3D")


def _tag_view3d_redraw(context=None):
    """Force visible 3D Viewports to redraw so overlays update immediately."""
    ctx = context or bpy.context
    try:
        screen = getattr(ctx, "screen", None) or getattr(bpy.context, "screen", None)
        if not screen:
            return
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:
        pass


def _ensure_hover_tracker(context=None):
    """Start a passive mouse tracker used by the optional viewport hover help."""
    if _hover_tool_state.get("running"):
        return
    try:
        bpy.ops.navtools.hint_mouse_tracker("INVOKE_DEFAULT")
    except Exception:
        pass


def _set_space_attr(space, attr, value):
    if hasattr(space, attr):
        try:
            setattr(space, attr, value)
            return True
        except Exception:
            return False
    return False


def _enable_transform_gizmos(context, active_tool="MOVE"):
    settings = get_settings(context)
    if settings and not settings.enable_gizmo_visibility_on_tool_change:
        return

    screen = getattr(context, "screen", None)
    if not screen:
        return

    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for space in area.spaces:
            if space.type != "VIEW_3D":
                continue
            _set_space_attr(space, "show_gizmo", True)
            _set_space_attr(space, "show_gizmo_tool", True)
            _set_space_attr(space, "show_gizmo_object_translate", active_tool == "MOVE")
            _set_space_attr(space, "show_gizmo_object_rotate", active_tool == "ROTATE")
            _set_space_attr(space, "show_gizmo_object_scale", active_tool == "SCALE")


def _set_active_transform_tool(context, tool):
    settings = get_settings(context)
    scale_tool = "builtin.scale"
    if settings and settings.scale_tool_mode == "SCALE_CAGE":
        scale_tool = "builtin.scale_cage"

    tool_ids = {
        "MOVE": "builtin.move",
        "ROTATE": "builtin.rotate",
        "SCALE": scale_tool,
    }
    tool_id = tool_ids.get(tool, "builtin.move")

    try:
        bpy.ops.wm.tool_set_by_id(name=tool_id, space_type="VIEW_3D")
    except TypeError:
        try:
            bpy.ops.wm.tool_set_by_id(name=tool_id)
        except Exception:
            return False
    except Exception:
        if tool == "SCALE" and tool_id != "builtin.scale":
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin.scale", space_type="VIEW_3D")
            except TypeError:
                try:
                    bpy.ops.wm.tool_set_by_id(name="builtin.scale")
                except Exception:
                    return False
            except Exception:
                return False
        else:
            return False

    _enable_transform_gizmos(context, tool)
    return True


def _transform_keys_should_pass_through(context):
    settings = get_settings(context)
    if not settings:
        return False
    if settings.transform_key_scope == "OBJECT_ONLY" and context.mode != "OBJECT":
        return True
    return False


def _invoke_scale_transform(axis="UNIFORM", increment_snap=False):
    axis_map = {
        "X": (True, False, False),
        "Y": (False, True, False),
        "Z": (False, False, True),
        "XY": (True, True, False),
        "XZ": (True, False, True),
        "YZ": (False, True, True),
    }

    kwargs = {}
    constraint = axis_map.get(axis)
    if constraint is not None:
        kwargs["constraint_axis"] = constraint

    if increment_snap:
        # Use Blender's native increment snapping. NavTools no longer tries to
        # override the snap percentage because Blender's modal transform step is
        # version/keymap dependent and is more reliable when left native.
        kwargs["snap"] = True
        kwargs["snap_elements"] = {"INCREMENT"}

    try:
        return bpy.ops.transform.resize("INVOKE_DEFAULT", **kwargs)
    except TypeError:
        # Snap properties vary between Blender versions. Fall back gracefully.
        kwargs.pop("snap_elements", None)
        try:
            return bpy.ops.transform.resize("INVOKE_DEFAULT", **kwargs)
        except TypeError:
            if constraint is not None:
                try:
                    return bpy.ops.transform.resize("INVOKE_DEFAULT", constraint_axis=constraint)
                except TypeError:
                    pass
            return bpy.ops.transform.resize("INVOKE_DEFAULT")



# -----------------------------------------------------------------------------
# Conflict management - narrowly targeted and restorable
# -----------------------------------------------------------------------------

def _kmi_menu_name(kmi):
    try:
        return getattr(kmi.properties, "name", "")
    except Exception:
        return ""


def _kmi_has_mods(kmi, *, ctrl=False, shift=False, alt=False, oskey=False):
    return (
        bool(getattr(kmi, "ctrl", False)) == ctrl and
        bool(getattr(kmi, "shift", False)) == shift and
        bool(getattr(kmi, "alt", False)) == alt and
        bool(getattr(kmi, "oskey", False)) == oskey
    )


def _is_our_keymap_item(kmi):
    idname = getattr(kmi, "idname", "")
    if idname.startswith(("navtools.", "pro" + "nav.", "davetools.")):
        return True
    if idname == "wm.call_menu_pie" and _kmi_menu_name(kmi) in {"NAVTOOLS_MT_view_pie", "NAVTOOLS_MT_scale_axis_pie"}:
        return True
    return False


def _disable_keymap_item(km, kmi, reason=""):
    global _disabled_keymap_conflicts
    if _is_our_keymap_item(kmi):
        return False
    if not getattr(kmi, "active", True):
        return False
    # Avoid storing the same object more than once during repeated Apply calls.
    for existing_km, existing_kmi, _old_active, _reason in _disabled_keymap_conflicts:
        if existing_km == km and existing_kmi == kmi:
            return False
    try:
        old_active = bool(kmi.active)
        kmi.active = False
        _disabled_keymap_conflicts.append((km, kmi, old_active, reason))
        return True
    except Exception:
        return False


def restore_disabled_keymap_conflicts():
    """Restore only the specific keymap items NavTools disabled this session."""
    global _disabled_keymap_conflicts
    for km, kmi, old_active, _reason in reversed(_disabled_keymap_conflicts):
        try:
            kmi.active = old_active
        except Exception:
            pass
    _disabled_keymap_conflicts.clear()


def _iter_candidate_keyconfigs():
    wm = bpy.context.window_manager
    # User keyconfig is the safest place to affect active user-overrides.
    # Default is included as a best-effort fallback for Blender setups where the
    # default item wins before add-on keymaps. All edits are restored on disable.
    for attr in ("user", "default"):
        try:
            kc = getattr(wm.keyconfigs, attr, None)
        except Exception:
            kc = None
        if kc:
            yield kc


def disable_navtools_mesh_conflicts(settings):
    """Disable only the keymap conflicts needed for the NavTools layout.

    This is deliberately narrow and restored by Restore Blender Defaults:
    - E no-mod extrude is disabled only when W/E/R are enabled in ALL modes.
    - R no-mod rotate is disabled when NavTools W/E/R is enabled in ALL modes, so R can select the Scale Gizmo or start Scale.
    - Shift+E native action is disabled only when Shift+E is being used for Extrude.
    """
    if not settings or not getattr(settings, "use_navtools", False):
        return

    wants_all_mode_transform = (
        getattr(settings, "use_transform_gizmo_keys", False) and
        getattr(settings, "transform_key_scope", "OBJECT_ONLY") == "ALL"
    )

    for kc in _iter_candidate_keyconfigs():
        candidate_maps = []
        for km_name in ("Mesh", "3D View"):
            try:
                km = kc.keymaps.get(km_name)
            except Exception:
                km = None
            if km and km not in candidate_maps:
                candidate_maps.append(km)

        for km in candidate_maps:
            for kmi in list(km.keymap_items):
                if _is_our_keymap_item(kmi):
                    continue
                key = getattr(kmi, "type", "")
                value = getattr(kmi, "value", "")
                idname = getattr(kmi, "idname", "")
                if value not in {"PRESS", "CLICK", "ANY"}:
                    continue

                # E = Rotate in NavTools full transform mode, so native E extrude must move to Shift+E.
                if wants_all_mode_transform and key == "E" and _kmi_has_mods(kmi):
                    menu_name = _kmi_menu_name(kmi)
                    if (
                        idname.startswith("mesh.extrude")
                        or idname in {"view3d.edit_mesh_extrude_move_normal", "view3d.edit_mesh_extrude_individual_move"}
                        or "extrude" in idname.lower()
                        or "extrude" in menu_name.lower()
                    ):
                        _disable_keymap_item(km, kmi, "Move native E extrude to Shift+E")
                        continue

                # R = Scale in NavTools full transform mode, so native R rotate must move off R.
                if wants_all_mode_transform:
                    if key == "R" and _kmi_has_mods(kmi) and idname.startswith("transform.rotate"):
                        _disable_keymap_item(km, kmi, "Replace native R rotate with NavTools Scale")
                        continue

                # Shift+E becomes Extrude. Move Blender's existing Shift+E action to Ctrl+Shift+E.
                if getattr(settings, "use_modeling_shortcuts", False) and getattr(settings, "use_shift_e_extrude", False):
                    if key == "E" and _kmi_has_mods(kmi, shift=True):
                        _disable_keymap_item(km, kmi, "Move native Shift+E action to Ctrl+Shift+E")
                        continue

# -----------------------------------------------------------------------------
# Keymap management - intentionally add-on only
# -----------------------------------------------------------------------------

def _new_keymap_item(km, idname, key_type, value="PRESS", **mods):
    """Create a keymap item, trying head=True so NavTools wins without disabling Blender defaults."""
    props = {k: mods.pop(k) for k in list(mods.keys()) if k in {"alt", "ctrl", "shift", "oskey", "any", "key_modifier"}}
    try:
        return km.keymap_items.new(idname, key_type, value, head=True, **props)
    except TypeError:
        # Older Blender builds may not accept head=True.
        return km.keymap_items.new(idname, key_type, value, **props)


def _shortcut_spec(kmi):
    return (
        getattr(kmi, "idname", ""),
        getattr(kmi, "type", ""),
        getattr(kmi, "value", ""),
        bool(getattr(kmi, "alt", False)),
        bool(getattr(kmi, "ctrl", False)),
        bool(getattr(kmi, "shift", False)),
        bool(getattr(kmi, "oskey", False)),
    )


def _is_navtools_or_old_test_key(kmi):
    idname = getattr(kmi, "idname", "")
    if idname.startswith(("navtools.", "pro" + "nav.", "davetools.")):
        return True

    # Clean up native operators created by previous test builds,
    # but only inside the ADD-ON keyconfig, never the Blender default/user keymaps.
    old_native_specs = {
        ("view3d.rotate", "LEFTMOUSE", "PRESS", True, False, False, False),
        ("view3d.move", "MIDDLEMOUSE", "PRESS", True, False, False, False),
        ("view3d.zoom", "RIGHTMOUSE", "PRESS", True, False, False, False),
        ("view3d.walk", "RIGHTMOUSE", "PRESS", False, False, True, False),
    }
    if _shortcut_spec(kmi) in old_native_specs:
        return True

    if idname == "wm.call_menu_pie":
        try:
            menu_name = kmi.properties.name
        except Exception:
            menu_name = ""
        if menu_name in {"NAVTOOLS_MT_view_pie", "NAVTOOLS_MT_scale_axis_pie", "DAVETOOLS_MT_" + "maya" + "plus" + "_view_pie"}:
            return True

    return False


def remove_navtools_keymaps():
    """Remove add-on keymaps and restore NavTools' narrowly disabled conflicts."""
    global addon_keymaps
    restore_disabled_keymap_conflicts()
    wm = bpy.context.window_manager
    kc = getattr(wm.keyconfigs, "addon", None)
    if not kc:
        return

    for km, kmi in list(addon_keymaps):
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()

    # Fallback cleanup across all add-on keymaps. This removes leftovers from
    # earlier test scripts if Blender lost the runtime addon_keymaps list.
    for km in kc.keymaps:
        for kmi in list(km.keymap_items):
            if _is_navtools_or_old_test_key(kmi):
                try:
                    km.keymap_items.remove(kmi)
                except Exception:
                    pass


def _get_or_create_keymap(kc, name, space_type=None):
    km = kc.keymaps.get(name)
    if km:
        return km
    if space_type:
        return kc.keymaps.new(name=name, space_type=space_type)
    return kc.keymaps.new(name=name)


def _transform_keymap_names(settings):
    if settings.transform_key_scope == "ALL":
        return ("3D View", "Object Mode", "Mesh", "Curve", "Armature", "Pose")
    return ("3D View", "Object Mode")


def _scale_pie_shortcut_spec(settings=None):
    settings = settings or get_settings()
    choice = getattr(settings, "scale_axis_pie_shortcut", "CTRL_SHIFT_R") if settings else "CTRL_SHIFT_R"
    specs = {
        "CTRL_SHIFT_R": {"type": "R", "ctrl": True, "shift": True, "alt": False, "oskey": False, "label": "Ctrl + Shift + R"},
        "ALT_R": {"type": "R", "ctrl": False, "shift": False, "alt": True, "oskey": False, "label": "Alt + R"},
        "CTRL_ALT_R": {"type": "R", "ctrl": True, "shift": False, "alt": True, "oskey": False, "label": "Ctrl + Alt + R"},
        "ALT_S": {"type": "S", "ctrl": False, "shift": False, "alt": True, "oskey": False, "label": "Alt + S"},
    }
    return specs.get(choice, specs["CTRL_SHIFT_R"])


def _scale_pie_shortcut_label(settings=None):
    return _scale_pie_shortcut_spec(settings)["label"]


def add_navtools_keymaps():
    wm = bpy.context.window_manager
    kc = getattr(wm.keyconfigs, "addon", None)
    if not kc:
        return

    settings = get_settings()
    if not settings:
        return

    remove_navtools_keymaps()

    if not settings.use_navtools:
        return

    km_3d = _get_or_create_keymap(kc, "3D View", space_type="VIEW_3D")

    # Navigation: NavTools always includes the stable Alt-mouse navigation.
    # The old on/off toggle was removed from the UI because hiding it made it
    # too easy to accidentally disable the core of the tool.
    kmi = _new_keymap_item(km_3d, "view3d.rotate", "LEFTMOUSE", alt=True)
    addon_keymaps.append((km_3d, kmi))
    kmi = _new_keymap_item(km_3d, "view3d.move", "MIDDLEMOUSE", alt=True)
    addon_keymaps.append((km_3d, kmi))
    kmi = _new_keymap_item(km_3d, "view3d.zoom", "RIGHTMOUSE", alt=True)
    addon_keymaps.append((km_3d, kmi))

    if settings.use_frame_selected_f:
        kmi = _new_keymap_item(km_3d, "navtools.frame_selected_plus", "F")
        addon_keymaps.append((km_3d, kmi))

    # NavTools-only extras. Earlier test builds had a Maya/NavTools profile switch,
    # but NavTools is now the single workflow.
    if settings.use_view_pie:
        kmi = _new_keymap_item(km_3d, "wm.call_menu_pie", "Q", alt=True)
        kmi.properties.name = "NAVTOOLS_MT_view_pie"
        addon_keymaps.append((km_3d, kmi))

    # The old Alt+Shift+LMB quick snap experiment is intentionally not mapped now.
    # NavTools uses Blender's better native gesture: MMB orbit, then press Alt.

    if settings.use_frame_all_shift_a:
        kmi = _new_keymap_item(km_3d, "navtools.view_all_plus", "F", shift=True)
        addon_keymaps.append((km_3d, kmi))

    if settings.use_walk_rmb_shift:
        kmi = _new_keymap_item(km_3d, "view3d.walk", "RIGHTMOUSE", shift=True)
        addon_keymaps.append((km_3d, kmi))

    # Transform: add-on keymaps first. In full NavTools Edit Mode, very specific native
    # Mesh conflicts are disabled below and restored when NavTools is disabled.
    if settings.use_transform_gizmo_keys or settings.use_scale_shortcuts:
        for km_name in _transform_keymap_names(settings):
            try:
                tkm = _get_or_create_keymap(kc, km_name, space_type="VIEW_3D" if km_name == "3D View" else None)
            except Exception:
                continue

            if settings.use_transform_gizmo_keys:
                kmi = _new_keymap_item(tkm, "navtools.set_transform_tool", "W")
                kmi.properties.tool = "MOVE"
                addon_keymaps.append((tkm, kmi))

                kmi = _new_keymap_item(tkm, "navtools.set_transform_tool", "E")
                kmi.properties.tool = "ROTATE"
                addon_keymaps.append((tkm, kmi))

                if settings.r_key_action == "UNIFORM_SCALE":
                    kmi = _new_keymap_item(tkm, "navtools.scale_transform", "R")
                    kmi.properties.axis = "UNIFORM"
                    kmi.properties.increment_snap = False
                    addon_keymaps.append((tkm, kmi))
                else:
                    kmi = _new_keymap_item(tkm, "navtools.set_transform_tool", "R")
                    kmi.properties.tool = "SCALE"
                    addon_keymaps.append((tkm, kmi))

            if settings.use_scale_shortcuts:
                if settings.use_snapped_scale_shift_r:
                    kmi = _new_keymap_item(tkm, "navtools.scale_transform", "R", shift=True)
                    kmi.properties.axis = "UNIFORM"
                    kmi.properties.increment_snap = True
                    addon_keymaps.append((tkm, kmi))

                # Scale Axis Pie has been retired from the default NavTools workflow.

    # Modelling shortcuts: Mesh Edit Mode.
    # Register in both Mesh and 3D View keymaps so the shortcut is caught across
    # Blender versions/keymap presets. The operators themselves only run in Edit Mesh.
    if settings.use_modeling_shortcuts:
        modeling_kms = []
        for km_name, space_type in (("Mesh", None), ("3D View", "VIEW_3D")):
            try:
                km = _get_or_create_keymap(kc, km_name, space_type=space_type)
            except Exception:
                km = None
            if km and km not in modeling_kms:
                modeling_kms.append(km)

        for mesh_km in modeling_kms:
            if settings.use_shift_e_extrude:
                kmi = _new_keymap_item(mesh_km, "navtools.extrude_region_plus", "E", shift=True)
                addon_keymaps.append((mesh_km, kmi))

            if settings.use_ctrl_shift_e_edge_crease:
                kmi = _new_keymap_item(mesh_km, "navtools.edge_crease_plus", "E", ctrl=True, shift=True)
                addon_keymaps.append((mesh_km, kmi))

            if settings.use_ctrl_e_edge_menu:
                # Explicitly map Ctrl+E to a NavTools wrapper instead of relying on
                # Blender's native Edge Menu keymap, which can be displaced by
                # earlier test builds or different keymap presets.
                kmi = _new_keymap_item(mesh_km, "navtools.show_edge_menu", "E", ctrl=True)
                addon_keymaps.append((mesh_km, kmi))

    # Apply narrow conflict disables after adding NavTools keymaps.
    disable_navtools_mesh_conflicts(settings)


def apply_blender_nav_tweaks():
    settings = get_settings()
    if not settings:
        return []

    changed = []
    if _set_input_pref("use_rotate_around_active", bool(settings.use_rotate_around_selection)):
        changed.append("Orbit Around Selection")
    if _set_input_pref("use_zoom_to_mouse", bool(settings.use_zoom_to_mouse)):
        changed.append("Zoom to Mouse Position")
    if _set_input_pref("use_auto_perspective", bool(settings.use_auto_perspective)):
        changed.append("Auto Perspective")
    if _set_input_pref("use_mouse_depth_navigate", bool(settings.use_mouse_depth_navigation)):
        changed.append("Depth Navigation")
    return changed


def settings_updated(self, context):
    try:
        if self.use_navtools:
            add_navtools_keymaps()
            if getattr(self, "use_hover_tool_descriptions", False):
                _ensure_hover_tracker(context)
        else:
            remove_navtools_keymaps()
    except Exception:
        # Never let UI setting changes crash Blender.
        pass
    _tag_view3d_redraw(context)


# -----------------------------------------------------------------------------
# Display / snapping helpers
# -----------------------------------------------------------------------------

def _format_scale_snap_percent(value):
    """Return a readable artist-facing scale snap label, e.g. 10%."""
    try:
        value = float(value)
    except Exception:
        return "Native"
    if abs(value - round(value)) < 0.001:
        text = f"{round(value):.0f}"
    else:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _scale_snap_increment_label(settings=None):
    # Fixed display only. Blender still handles the actual native increment snapping.
    return "10%"


def _format_scale_increment_count_line(context):
    """Return a simple snapped-scale step counter based on the current scale readout.

    This is deliberately fixed at 10% because Blender's native increment snap
    behaves most consistently when NavTools does not try to override the snap size.
    """
    state = _scale_readout_state
    if not state.get("active"):
        return None

    base_extents = state.get("baseline_extents")
    current_extents = _selection_world_extents(context)
    if base_extents is None or current_extents is None:
        return None

    parts = _object_scale_percent_parts(context, state.get("baseline_scales"))
    if not parts:
        parts = _axis_percent_parts_from_extents(base_extents, current_extents)
    if not parts:
        return None

    values = [p for _axis, p in parts]
    pct = sum(values) / len(values)
    delta = pct - 100.0
    steps = int(round(delta / 10.0))
    sign = "+" if steps > 0 else ""
    return f"Increment Counter: {sign}{steps}"


def _selection_signature(context):
    """Small signature used to tell whether the scale readout still belongs to the current selection."""
    try:
        mode = getattr(context, "mode", "")
        if mode.startswith("EDIT"):
            obj = getattr(context, "edit_object", None)
            if not obj:
                return (mode, None)
            selected_count = 0
            if getattr(obj, "type", "") == "MESH" and bmesh is not None:
                try:
                    bm = bmesh.from_edit_mesh(obj.data)
                    selected_count = sum(1 for v in bm.verts if v.select)
                except Exception:
                    selected_count = 0
            return (mode, getattr(obj, "name", ""), selected_count)

        names = tuple(sorted(getattr(obj, "name", "") for obj in (getattr(context, "selected_objects", []) or []) if obj))
        return (mode, names)
    except Exception:
        return (getattr(context, "mode", ""), None)


def _selection_world_extents(context):
    """Return selected world-space bounding-box extents for live scale comparison."""
    if Vector is None:
        return None

    points = []
    mode = getattr(context, "mode", "")

    if mode.startswith("EDIT"):
        obj = getattr(context, "edit_object", None)
        if not obj:
            return None
        if getattr(obj, "type", "") == "MESH" and bmesh is not None:
            try:
                bm = bmesh.from_edit_mesh(obj.data)
                selected_verts = [v for v in bm.verts if v.select]
                if selected_verts:
                    mw = obj.matrix_world
                    points = [mw @ v.co for v in selected_verts]
            except Exception:
                points = []
        if not points:
            try:
                points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            except Exception:
                points = []
    else:
        selected = list(getattr(context, "selected_objects", []) or [])
        if not selected:
            obj = getattr(context, "active_object", None)
            selected = [obj] if obj else []
        for obj in selected:
            if not obj:
                continue
            try:
                points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
            except Exception:
                try:
                    points.append(obj.matrix_world.translation.copy())
                except Exception:
                    pass

    if not points:
        return None

    try:
        min_v = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
        max_v = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
        return max_v - min_v
    except Exception:
        return None


def _selection_object_scales(context):
    """Return selected Object Mode local scale values for a more reliable scale readout."""
    if getattr(context, "mode", "") != "OBJECT":
        return None
    selected = list(getattr(context, "selected_objects", []) or [])
    if not selected:
        obj = getattr(context, "active_object", None)
        selected = [obj] if obj else []
    result = {}
    for obj in selected:
        if not obj:
            continue
        try:
            result[getattr(obj, "name", "")] = (float(obj.scale.x), float(obj.scale.y), float(obj.scale.z))
        except Exception:
            pass
    return result or None


def _object_scale_percent_parts(context, baseline_scales):
    """Return X/Y/Z percent scale from Object Mode scale values against the captured baseline."""
    if getattr(context, "mode", "") != "OBJECT" or not baseline_scales:
        return None
    selected = list(getattr(context, "selected_objects", []) or [])
    if not selected:
        obj = getattr(context, "active_object", None)
        selected = [obj] if obj else []
    ratios = []
    for obj in selected:
        if not obj:
            continue
        name = getattr(obj, "name", "")
        base = baseline_scales.get(name)
        if not base:
            continue
        try:
            current = (float(obj.scale.x), float(obj.scale.y), float(obj.scale.z))
            obj_ratios = []
            for b, c in zip(base, current):
                if abs(b) < 1e-8:
                    obj_ratios.append(100.0)
                else:
                    obj_ratios.append((c / b) * 100.0)
            ratios.append(obj_ratios)
        except Exception:
            pass
    if not ratios:
        return None
    parts = []
    for axis_name, index in (("X", 0), ("Y", 1), ("Z", 2)):
        vals = [r[index] for r in ratios if len(r) > index]
        if vals:
            parts.append((axis_name, sum(vals) / len(vals)))
    return parts or None


def _start_scale_readout(context, axis="UNIFORM", increment_snap=False):
    """Capture the selection size just before Blender's native scale modal starts."""
    global _scale_readout_state
    extents = _selection_world_extents(context)
    if extents is None:
        _scale_readout_state["active"] = False
        return
    _scale_readout_state.update({
        "active": True,
        "signature": _selection_signature(context),
        "baseline_extents": extents.copy(),
        "baseline_scales": _selection_object_scales(context),
        "axis": axis,
        "increment_snap": bool(increment_snap),
        "started_at": time.monotonic(),
        "last_extents": extents.copy(),
        "last_change_at": time.monotonic(),
    })


def _reset_scale_readout():
    global _scale_readout_state
    _scale_readout_state.update({
        "active": False,
        "signature": None,
        "baseline_extents": None,
        "baseline_scales": None,
        "axis": "UNIFORM",
        "increment_snap": False,
        "started_at": 0.0,
        "last_extents": None,
        "last_change_at": 0.0,
    })


def _axis_percent_parts_from_extents(base_extents, current_extents):
    parts = []
    axes = (("X", 0), ("Y", 1), ("Z", 2))
    for axis_name, index in axes:
        try:
            base = float(base_extents[index])
            current = float(current_extents[index])
        except Exception:
            continue
        if abs(base) < 1e-6:
            continue
        parts.append((axis_name, (current / base) * 100.0))
    return parts


def _format_scale_change_line(context):
    """Return a live scale line such as 'Current Scale: 140% (+40%)'."""
    state = _scale_readout_state
    if not state.get("active"):
        return None
    if state.get("signature") != _selection_signature(context):
        _reset_scale_readout()
        return None

    base_extents = state.get("baseline_extents")
    current_extents = _selection_world_extents(context)
    if base_extents is None or current_extents is None:
        return None

    # Native Blender's transform modal does not tell add-ons when it has finished,
    # so keep the readout live while the scale is changing and hide it shortly
    # after movement stops. This avoids the snap label sitting on-screen all the time.
    now = time.monotonic()
    last_extents = state.get("last_extents")
    changed = False
    if last_extents is None:
        changed = True
    else:
        try:
            changed = (current_extents - last_extents).length > 1e-5
        except Exception:
            changed = True
    started_at = float(state.get("started_at", now) or now)
    if changed:
        state["last_extents"] = current_extents.copy()
        state["last_change_at"] = now
    else:
        # Keep the readout visible until the user confirms/cancels the scale
        # gesture. The passive event tracker clears this state on Enter/LMB/ESC/RMB.
        # Native Blender transform preview data is not always committed while the
        # drag is live, so a timeout here makes the readout vanish too early.
        pass

    # Object Mode is most accurately represented by the object's own local scale.
    # Edit Mode falls back to selected geometry extents.
    parts = _object_scale_percent_parts(context, state.get("baseline_scales"))
    if not parts:
        parts = _axis_percent_parts_from_extents(base_extents, current_extents)
    if not parts:
        return None

    values = [p for _axis, p in parts]
    # If the axes are close together, present it as one readable uniform value.
    if max(values) - min(values) <= 2.0 or len(values) == 1:
        pct = sum(values) / len(values)
        delta = pct - 100.0
        return f"Current Scale: {pct:.0f}% ({delta:+.0f}%)"

    # For non-uniform scaling, show concise per-axis values.
    axis_text = "  ".join(f"{axis} {pct:.0f}%" for axis, pct in parts)
    return f"Current Scale: {axis_text}"


def apply_scale_snap_increment_to_viewports(context=None):
    """Legacy no-op. NavTools uses Blender's native scale increment snapping."""
    return []

def snap_increment_updated(self, context):
    _tag_view3d_redraw(context)


def nav_tweaks_updated(self, context):
    # Keep these toggles responsive when used from the Quality of Life or
    # Navigation Tweaks sections. They are still restorable via Restore Navigation Tweaks.
    try:
        if getattr(self, "use_navtools", False):
            apply_blender_nav_tweaks()
    except Exception:
        pass

# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------

def overlay_settings_updated(self, context):
    _tag_view3d_redraw(context)

class NAVTOOLS_PG_settings(PropertyGroup):
    use_navtools: BoolProperty(
        name="Enable NavTools",
        description="Use NavTools navigation and optional transform helper shortcuts",
        default=False,
        update=settings_updated,
    )


    show_description_panel: BoolProperty(
        name="Descriptions",
        description="Legacy global description panel. Per-section info toggles are used instead.",
        default=True,
        options={"HIDDEN"},
    )

    has_opened_description_once: BoolProperty(
        name="Has opened description once",
        default=True,
        options={"HIDDEN"},
    )

    # Per-section info toggles. These are intentionally off by default so the
    # main panel stays compact; click the information icon in a section to show
    # its notes.
    info_status: BoolProperty(name="Show Status Info", default=False)
    info_qol: BoolProperty(name="Show Quality of Life Info", default=False)
    info_nav_tweaks: BoolProperty(name="Show Navigation Tweaks Info", default=False)
    info_transform: BoolProperty(name="Show Transform Info", default=False)
    info_scale: BoolProperty(name="Show Scale Info", default=False)
    info_modeling: BoolProperty(name="Show Modelling Info", default=False)
    info_hints: BoolProperty(name="Show On-screen Hints Info", default=False)
    info_actions: BoolProperty(name="Show Restore Info", default=False)

    navigation_profile: EnumProperty(
        name="Navigation Profile",
        items=(
            ("MAYA", "Maya", "Maya-style navigation only"),
            ("NAVTOOLS", "NavTools", "Maya-style navigation plus selected Blender/game-artist extras"),
        ),
        default="NAVTOOLS",
        update=settings_updated,
    )

    use_alt_mouse_navigation: BoolProperty(
        name="NavTools Alt Mouse Navigation",
        description="Alt + LMB orbit, Alt + MMB pan, Alt + RMB zoom. Use Restore Blender Defaults to return to Blender navigation.",
        default=True,
        update=settings_updated,
    )

    use_frame_selected_f: BoolProperty(
        name="F = Frame Selected",
        description="Use F to frame selected objects/components",
        default=True,
        update=settings_updated,
    )

    use_view_pie: BoolProperty(
        name="Alt + Q: Maya-style View Pie",
        description="Open a Maya-style view pie.",
        default=True,
        update=settings_updated,
    )

    use_snap_nearest_ortho: BoolProperty(
        name="Extra Alt + Shift + LMB: Quick snap view",
        description="Optional one-click snap. Recommended snap remains Blender native: MMB orbit, then press Alt",
        default=False,
        update=settings_updated,
    )

    use_frame_all_shift_a: BoolProperty(
        name="Shift + F: Frame All",
        default=False,
        update=settings_updated,
    )

    use_walk_rmb_shift: BoolProperty(
        name="Shift + RMB: Unreal-style Navigation",
        default=False,
        update=settings_updated,
    )

    show_native_snap_view_hint: BoolProperty(
        name="MMB + Alt = Snap to Nearest View Plane",
        description="Show Blender's native snap-view gesture in the overlay: middle-mouse orbit, then press Alt",
        default=True,
    )

    apply_nav_tweaks_with_enable: BoolProperty(
        name="Apply Navigation Tweaks on enable",
        default=True,
    )

    use_rotate_around_selection: BoolProperty(name="Orbit Around Selection", default=True, update=nav_tweaks_updated)
    use_zoom_to_mouse: BoolProperty(name="Zoom to Mouse Position", default=True, update=nav_tweaks_updated)
    use_auto_perspective: BoolProperty(name="Snap result: Ortho", default=True, update=nav_tweaks_updated)
    use_mouse_depth_navigation: BoolProperty(name="Depth Navigation", default=True, update=nav_tweaks_updated)

    use_transform_gizmo_keys: BoolProperty(
        name="W/E/R transform keys",
        description="W = Move, E = Rotate, R = Scale Gizmo by default",
        default=True,
        update=settings_updated,
    )

    r_key_action: EnumProperty(
        name="R key action",
        items=(
            ("UNIFORM_SCALE", "Uniform Scale", "Press R to start interactive uniform scale"),
            ("SCALE_GIZMO", "Scale Gizmo", "Press R to activate Blender's scale gizmo tool"),
        ),
        default="SCALE_GIZMO",
        update=settings_updated,
    )

    transform_key_scope: EnumProperty(
        name="Transform key scope",
        items=(
            ("OBJECT_ONLY", "Object Mode only", "Safest: NavTools W/E/R in Object Mode, Blender edit-mode keys remain intact"),
            ("ALL", "Object + Edit Mode", "NavTools layout: W/E/R work in Object and Edit Mode; Shift+E becomes Extrude"),
        ),
        default="ALL",
        update=settings_updated,
    )

    enable_gizmo_visibility_on_tool_change: BoolProperty(
        name="Force transform gizmos visible",
        default=True,
    )

    scale_tool_mode: EnumProperty(
        name="R scale tool",
        items=(
            ("SCALE", "Scale Gizmo", "Use Blender's standard Scale tool"),
            ("SCALE_CAGE", "Scale Cage", "Use Blender's Scale Cage tool where available"),
        ),
        default="SCALE",
        update=settings_updated,
    )

    use_scale_shortcuts: BoolProperty(
        name="Scale Helpers",
        description="Show scale helpers",
        default=True,
        update=settings_updated,
    )

    use_snapped_scale_shift_r: BoolProperty(
        name="Shift + R: Increment Scale",
        description="Increment Scale. Fixed 10% incremental scale.",
        default=True,
        update=settings_updated,
    )

    use_scale_axis_pie: BoolProperty(
        name="Scale Axis Pie",
        description="Retired option. Axis and plane scale are handled by Blender's native transform constraints after starting Scale.",
        default=False,
        update=settings_updated,
        options={"HIDDEN"},
    )


    show_scale_snap_near_gizmo: BoolProperty(
        name="Show scale snap label near gizmo",
        description="Legacy experimental overlay. Kept off because Blender native scale does not expose a reliable active-state hook to add-ons.",
        default=True,
        options={"HIDDEN"},
    )


    scale_axis_pie_shortcut: EnumProperty(
        name="Scale pie shortcut",
        items=(
            ("CTRL_SHIFT_R", "Ctrl + Shift + R", "Recommended; avoids NVIDIA Alt+R overlay conflicts"),
            ("ALT_R", "Alt + R", "May be intercepted by NVIDIA/other overlays"),
            ("CTRL_ALT_R", "Ctrl + Alt + R", "Alternative R-based shortcut"),
            ("ALT_S", "Alt + S", "Alternative scale-related shortcut"),
        ),
        default="CTRL_SHIFT_R",
        update=settings_updated,
    )

    use_modeling_shortcuts: BoolProperty(
        name="Modelling shortcuts",
        description="Mesh Edit Mode shortcuts for common modelling operations",
        default=True,
        update=settings_updated,
    )

    use_shift_e_extrude: BoolProperty(
        name="Shift + E: Extrude",
        description="Easy NavTools extrude shortcut in Mesh Edit Mode",
        default=True,
        update=settings_updated,
    )

    use_ctrl_shift_e_edge_crease: BoolProperty(
        name="Ctrl + Shift + E: Original Shift + E",
        description="Moves Blender's original Shift + E edge-crease action to Ctrl + Shift + E",
        default=True,
        update=settings_updated,
    )

    use_ctrl_e_edge_menu: BoolProperty(
        name="Ctrl + E: Edge Menu",
        description="Explicit NavTools shortcut for Blender's Mesh Edge menu",
        default=True,
        update=settings_updated,
    )

    use_selection_hints: BoolProperty(
        name="Show Hints",
        description="Show a larger translucent NavTools shortcut cheat sheet in the viewport when something is selected",
        default=True,
        update=overlay_settings_updated,
    )

    hint_overlay_position: EnumProperty(
        name="Hint Position",
        items=(
            ("TOP_LEFT", "Top Left", "Show hints at the top-left of the viewport"),
            ("TOP_RIGHT", "Top Right", "Show hints at the top-right of the viewport"),
            ("BOTTOM_LEFT", "Bottom Left", "Show hints at the bottom-left of the viewport"),
            ("BOTTOM_RIGHT", "Bottom Right", "Show hints at the bottom-right of the viewport"),
        ),
        default="BOTTOM_LEFT",
        update=overlay_settings_updated,
    )

    hint_overlay_width: FloatProperty(
        name="Hint panel width",
        description="Internal fixed width for the on-screen NavTools hint panel.",
        default=640.0,
        min=360.0,
        soft_min=480.0,
        soft_max=900.0,
        precision=0,
        update=overlay_settings_updated,
        options={"HIDDEN"},
    )

    use_hover_tool_descriptions: BoolProperty(
        name="Show hover descriptions",
        description="Legacy hover help. The reliable description box below is recommended instead.",
        default=False,
        update=overlay_settings_updated,
    )

    use_hint_detail_box: BoolProperty(
        name="Show description box",
        description="Show the old viewport description box next to the NavTools cheat sheet",
        default=False,
        update=overlay_settings_updated,
    )

    hint_detail_topic: EnumProperty(
        name="Description topic",
        items=(
            ("AUTO", "Auto", "Choose the most useful overview for the current mode"),
            ("OVERVIEW", "Overview", "Describe what NavTools changes overall"),
            ("NAVIGATION", "Navigation", "Explain Maya-style viewport navigation"),
            ("TRANSFORM", "Transform", "Explain W/E/R transform controls and axis constraints"),
            ("SCALE", "Scale", "Explain scale, snapped scale, and the percentage readout"),
            ("QOL", "Quality of Life", "Explain the optional navigation extras"),
            ("SHORTCUTS", "Edit Shortcuts", "Explain the remapped Edit Mode shortcuts"),
        ),
        default="AUTO",
        update=overlay_settings_updated,
    )

    hover_description_width: FloatProperty(
        name="Description box width",
        description="Width of the large description box, in pixels",
        default=520.0,
        min=320.0,
        soft_min=420.0,
        soft_max=860.0,
        precision=0,
        update=overlay_settings_updated,
    )

    # Collapsible UI state
    show_status_section: BoolProperty(name="Status", default=True)
    show_core_nav_section: BoolProperty(name="Core Navigation", default=True)
    show_nav_extras_section: BoolProperty(name="Quality of Life Extras", default=True)
    show_native_tweaks_section: BoolProperty(name="Navigation Tweaks", default=False)
    show_transform_section: BoolProperty(name="Transform Keys", default=True)
    show_scale_section: BoolProperty(name="Helpers", default=True)
    show_modeling_section: BoolProperty(name="Modelling Shortcuts", default=True)
    show_hint_section: BoolProperty(name="On-screen Hints", default=False, options={"HIDDEN"})
    show_actions_section: BoolProperty(name="Restore", default=True)
    show_manual_section: BoolProperty(name="Manual Test Buttons", default=False, options={"HIDDEN"})


# -----------------------------------------------------------------------------
# Operators
# -----------------------------------------------------------------------------

class NAVTOOLS_OT_enable_tools(Operator):
    bl_idname = "navtools.enable_tools"
    bl_label = "Enable NavTools"
    bl_description = "Enable NavTools navigation and optional transform helper keymaps"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = get_settings(context)
        if settings:
            settings.use_navtools = True
            # Descriptions are now per-section info toggles, off by default.
            settings.show_description_panel = False
            settings.use_hint_detail_box = False
            settings.use_hover_tool_descriptions = False
            _reset_scale_readout()
            settings.use_alt_mouse_navigation = True
            add_navtools_keymaps()
            apply_blender_nav_tweaks()
            _ensure_hover_tracker(context)
        self.report({"INFO"}, "NavTools enabled")
        return {"FINISHED"}




def _restore_navtools_default_settings(settings):
    """Restore NavTools' own options to the intended default workflow.

    This does not remove Blender keymaps by itself; it resets NavTools settings and
    then the restore operator reapplies/removes add-on keymaps as needed.
    """
    defaults = {
        "use_navtools": True,
        "show_description_panel": False,
        "has_opened_description_once": False,
        "navigation_profile": "NAVTOOLS",
        "use_alt_mouse_navigation": True,
        "use_frame_selected_f": True,
        "use_view_pie": True,
        "use_snap_nearest_ortho": False,
        "use_frame_all_shift_a": False,
        "use_walk_rmb_shift": False,
        "show_native_snap_view_hint": True,
        "apply_nav_tweaks_with_enable": True,
        "use_rotate_around_selection": True,
        "use_zoom_to_mouse": True,
        "use_auto_perspective": True,
        "use_mouse_depth_navigation": True,
        "use_transform_gizmo_keys": True,
        "r_key_action": "SCALE_GIZMO",
        "transform_key_scope": "ALL",
        "enable_gizmo_visibility_on_tool_change": True,
        "scale_tool_mode": "SCALE",
        "use_scale_shortcuts": True,
        "use_snapped_scale_shift_r": True,
        "use_scale_axis_pie": False,
        "show_scale_snap_near_gizmo": True,
        "scale_axis_pie_shortcut": "CTRL_SHIFT_R",
        "use_modeling_shortcuts": True,
        "use_shift_e_extrude": True,
        "use_ctrl_shift_e_edge_crease": True,
        "use_ctrl_e_edge_menu": True,
        "use_selection_hints": True,
        "hint_overlay_position": "BOTTOM_LEFT",
        "hint_overlay_width": 640.0,
        "use_hover_tool_descriptions": False,
        "use_hint_detail_box": False,
        "hint_detail_topic": "AUTO",
        "hover_description_width": 520.0,
        "info_status": False,
        "info_qol": False,
        "info_nav_tweaks": False,
        "info_transform": False,
        "info_scale": False,
        "info_modeling": False,
        "info_hints": False,
        "info_actions": False,
        "show_status_section": True,
        "show_core_nav_section": True,
        "show_nav_extras_section": True,
        "show_native_tweaks_section": True,
        "show_transform_section": True,
        "show_scale_section": True,
        "show_modeling_section": True,
        "show_hint_section": False,
        "show_actions_section": True,
        "show_manual_section": False,
    }
    for name, value in defaults.items():
        try:
            setattr(settings, name, value)
        except Exception:
            pass


class NAVTOOLS_OT_restore_navtools_defaults(Operator):
    bl_idname = "navtools.restore_navtools_defaults"
    bl_label = "Restore NavTools Defaults"
    bl_description = "Reset NavTools' own settings to the recommended default layout without restoring Blender's native defaults"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = get_settings(context)
        if not settings:
            self.report({"WARNING"}, "NavTools settings unavailable")
            return {"CANCELLED"}

        _restore_navtools_default_settings(settings)
        _reset_scale_readout()
        add_navtools_keymaps()
        self.report({"INFO"}, "NavTools defaults restored")
        return {"FINISHED"}

class NAVTOOLS_OT_apply_settings(Operator):
    bl_idname = "navtools.apply_settings"
    bl_label = "Refresh NavTools Shortcuts"
    bl_description = "Deprecated internal refresh operator; settings refresh automatically"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = get_settings(context)
        if settings and settings.use_navtools:
            settings.use_hint_detail_box = False
            settings.use_hover_tool_descriptions = False
            _reset_scale_readout()
            settings.use_alt_mouse_navigation = True
            add_navtools_keymaps()
            apply_blender_nav_tweaks()
            _ensure_hover_tracker(context)
            self.report({"INFO"}, "NavTools settings applied")
        else:
            _reset_scale_readout()
            remove_navtools_keymaps()
            self.report({"INFO"}, "NavTools keymaps removed")
        return {"FINISHED"}


class NAVTOOLS_OT_restore_blender_defaults(Operator):
    bl_idname = "navtools.restore_blender_defaults"
    bl_label = "Restore Blender Defaults"
    bl_description = "Remove NavTools add-on keymaps and restore native navigation tweaks changed this session"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = get_settings(context)
        if settings:
            settings.use_navtools = False
        remove_navtools_keymaps()
        _restore_input_prefs()
        _hover_tool_state["running"] = False
        _hover_tool_state["mouse_window_x"] = -10000
        _hover_tool_state["mouse_window_y"] = -10000
        self.report({"INFO"}, "NavTools keymaps removed")
        return {"FINISHED"}


class NAVTOOLS_OT_apply_nav_tweaks(Operator):
    bl_idname = "navtools.apply_nav_tweaks"
    bl_label = "Apply Navigation Tweaks"
    bl_options = {"REGISTER"}

    def execute(self, context):
        changed = apply_blender_nav_tweaks()
        if changed:
            self.report({"INFO"}, "Applied: " + ", ".join(changed))
        else:
            self.report({"INFO"}, "No native nav tweaks changed")
        return {"FINISHED"}


class NAVTOOLS_OT_restore_nav_tweaks(Operator):
    bl_idname = "navtools.restore_nav_tweaks"
    bl_label = "Restore Navigation Tweaks"
    bl_options = {"REGISTER"}

    def execute(self, context):
        _restore_input_prefs()
        self.report({"INFO"}, "Restored navigation tweaks changed this session")
        return {"FINISHED"}




class NAVTOOLS_OT_frame_selected_plus(Operator):
    bl_idname = "navtools.frame_selected_plus"
    bl_label = "Frame Selected"
    bl_description = "Frame selected objects/components in the viewport"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _space_is_view3d(context)

    def execute(self, context):
        try:
            bpy.ops.view3d.view_selected(use_all_regions=False)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"WARNING"}, f"Could not frame selection: {exc}")
            return {"CANCELLED"}


class NAVTOOLS_OT_view_all_plus(Operator):
    bl_idname = "navtools.view_all_plus"
    bl_label = "Frame All Plus"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _space_is_view3d(context)

    def execute(self, context):
        try:
            bpy.ops.view3d.view_all(center=False)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"WARNING"}, f"Could not frame all: {exc}")
            return {"CANCELLED"}


class NAVTOOLS_OT_toggle_perspective_ortho(Operator):
    bl_idname = "navtools.toggle_perspective_ortho"
    bl_label = "Toggle Perspective / Orthographic"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _space_is_view3d(context)

    def execute(self, context):
        try:
            bpy.ops.view3d.view_persportho()
            return {"FINISHED"}
        except Exception as exc:
            self.report({"WARNING"}, f"Could not toggle perspective/ortho: {exc}")
            return {"CANCELLED"}


class NAVTOOLS_OT_view_axis(Operator):
    bl_idname = "navtools.view_axis"
    bl_label = "View Axis"
    bl_options = {"REGISTER", "UNDO"}

    axis: EnumProperty(
        name="Axis",
        items=(
            ("FRONT", "Front", "Front view"),
            ("BACK", "Back", "Back view"),
            ("LEFT", "Left", "Left view"),
            ("RIGHT", "Right", "Right view"),
            ("TOP", "Top", "Top view"),
            ("BOTTOM", "Bottom", "Bottom view"),
        ),
        default="FRONT",
    )

    @classmethod
    def poll(cls, context):
        return _space_is_view3d(context)

    def execute(self, context):
        try:
            bpy.ops.view3d.view_axis(type=self.axis, align_active=False)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"WARNING"}, f"Could not set view axis: {exc}")
            return {"CANCELLED"}


class NAVTOOLS_OT_snap_nearest_ortho(Operator):
    bl_idname = "navtools.snap_nearest_ortho"
    bl_label = "Snap to Nearest Ortho View"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _space_is_view3d(context) and Vector is not None

    def execute(self, context):
        space = getattr(context, "space_data", None)
        rv3d = getattr(space, "region_3d", None)
        if not rv3d:
            return {"CANCELLED"}
        view_direction = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
        if view_direction.length == 0:
            return {"CANCELLED"}
        view_direction.normalize()
        candidates = {
            "FRONT": Vector((0.0, -1.0, 0.0)),
            "BACK": Vector((0.0, 1.0, 0.0)),
            "RIGHT": Vector((1.0, 0.0, 0.0)),
            "LEFT": Vector((-1.0, 0.0, 0.0)),
            "TOP": Vector((0.0, 0.0, -1.0)),
            "BOTTOM": Vector((0.0, 0.0, 1.0)),
        }
        best_axis = max(candidates, key=lambda axis: view_direction.dot(candidates[axis]))
        bpy.ops.view3d.view_axis(type=best_axis, align_active=False)
        return {"FINISHED"}


class NAVTOOLS_OT_set_transform_tool(Operator):
    bl_idname = "navtools.set_transform_tool"
    bl_label = "Set Transform Tool"
    bl_options = {"REGISTER"}

    tool: EnumProperty(
        name="Tool",
        items=(
            ("MOVE", "Move", "Move gizmo"),
            ("ROTATE", "Rotate", "Rotate gizmo"),
            ("SCALE", "Scale", "Scale gizmo"),
        ),
        default="MOVE",
    )

    @classmethod
    def poll(cls, context):
        return _space_is_view3d(context)

    def invoke(self, context, event):
        if _transform_keys_should_pass_through(context):
            return {"PASS_THROUGH"}
        return self.execute(context)

    def execute(self, context):
        if _transform_keys_should_pass_through(context):
            return {"PASS_THROUGH"}
        if _set_active_transform_tool(context, self.tool):
            return {"FINISHED"}
        self.report({"WARNING"}, "Could not set transform tool")
        return {"CANCELLED"}


class NAVTOOLS_OT_scale_transform(Operator):
    bl_idname = "navtools.scale_transform"
    bl_label = "NavTools Scale Transform"
    bl_description = "Start Blender's native interactive scale; then use X/Y/Z, Shift+X/Y/Z, or double-tap axes for constraints"
    bl_options = {"REGISTER", "UNDO"}

    axis: EnumProperty(
        name="Axis",
        items=(
            ("UNIFORM", "Uniform", "Scale uniformly"),
            ("X", "X", "Scale on X only"),
            ("Y", "Y", "Scale on Y only"),
            ("Z", "Z", "Scale on Z only"),
            ("XY", "XY", "Scale on X and Y"),
            ("XZ", "XZ", "Scale on X and Z"),
            ("YZ", "YZ", "Scale on Y and Z"),
        ),
        default="UNIFORM",
    )

    increment_snap: BoolProperty(name="Increment Snap", default=False)

    @classmethod
    def poll(cls, context):
        return _space_is_view3d(context)

    def invoke(self, context, event):
        if _transform_keys_should_pass_through(context):
            return {"PASS_THROUGH"}
        try:
            _start_scale_readout(context, self.axis, self.increment_snap)
            return _invoke_scale_transform(self.axis, self.increment_snap)
        except Exception as exc:
            _reset_scale_readout()
            self.report({"WARNING"}, f"Could not start scale transform: {exc}")
            return {"CANCELLED"}

    def execute(self, context):
        if _transform_keys_should_pass_through(context):
            return {"PASS_THROUGH"}
        try:
            _start_scale_readout(context, self.axis, self.increment_snap)
            return _invoke_scale_transform(self.axis, self.increment_snap)
        except Exception as exc:
            _reset_scale_readout()
            self.report({"WARNING"}, f"Could not start scale transform: {exc}")
            return {"CANCELLED"}


class NAVTOOLS_OT_show_scale_axis_pie(Operator):
    bl_idname = "navtools.show_scale_axis_pie"
    bl_label = "Show Scale Axis Pie"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _space_is_view3d(context)

    def invoke(self, context, event):
        try:
            bpy.ops.wm.call_menu_pie(name="NAVTOOLS_MT_scale_axis_pie")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"WARNING"}, f"Could not open scale pie: {exc}")
            return {"CANCELLED"}

    def execute(self, context):
        return self.invoke(context, None)




class NAVTOOLS_OT_extrude_region_plus(Operator):
    bl_idname = "navtools.extrude_region_plus"
    bl_label = "NavTools Extrude"
    bl_description = "Extrude selected mesh components using Blender's native interactive View3D extrude behaviour"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "edit_object", None)
        return _space_is_view3d(context) and context.mode == "EDIT_MESH" and obj is not None

    def _native_extrude(self):
        # This is the operator Blender normally uses for E in Mesh Edit Mode.
        # It keeps the transform modal state, so axis constraints such as X/Y/Z
        # and double-tap local constraints behave like native Blender extrude.
        try:
            return bpy.ops.view3d.edit_mesh_extrude_move_normal("INVOKE_DEFAULT")
        except Exception:
            # Compatibility fallback for unusual keymaps/versions.
            try:
                return bpy.ops.mesh.extrude_region_move("INVOKE_DEFAULT")
            except Exception:
                bpy.ops.mesh.extrude_region("INVOKE_DEFAULT")
                return bpy.ops.transform.translate("INVOKE_DEFAULT")

    def invoke(self, context, event):
        try:
            return self._native_extrude()
        except Exception as exc:
            self.report({"WARNING"}, f"Could not extrude: {exc}")
            return {"CANCELLED"}

    def execute(self, context):
        try:
            return self._native_extrude()
        except Exception as exc:
            self.report({"WARNING"}, f"Could not extrude: {exc}")
            return {"CANCELLED"}


class NAVTOOLS_OT_edge_crease_plus(Operator):
    bl_idname = "navtools.edge_crease_plus"
    bl_label = "NavTools Edge Crease"
    bl_description = "Run Blender's original Shift + E edge crease action"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "edit_object", None)
        return _space_is_view3d(context) and context.mode == "EDIT_MESH" and obj is not None

    def _native_edge_crease(self):
        # Blender's default Shift + E action in Mesh Edit Mode is normally Edge Crease.
        # Operator names have been stable for years, but keep a fallback for unusual builds.
        try:
            return bpy.ops.transform.edge_crease("INVOKE_DEFAULT")
        except Exception:
            try:
                return bpy.ops.wm.call_menu(name="VIEW3D_MT_edit_mesh_edges")
            except Exception:
                raise

    def invoke(self, context, event):
        try:
            return self._native_edge_crease()
        except Exception as exc:
            self.report({"WARNING"}, f"Could not run edge crease/original Shift+E action: {exc}")
            return {"CANCELLED"}

    def execute(self, context):
        return self.invoke(context, None)


class NAVTOOLS_OT_show_edge_menu(Operator):
    bl_idname = "navtools.show_edge_menu"
    bl_label = "NavTools Edge Menu"
    bl_description = "Open Blender's Mesh Edge menu"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "edit_object", None)
        return _space_is_view3d(context) and context.mode == "EDIT_MESH" and obj is not None

    def invoke(self, context, event):
        try:
            bpy.ops.wm.call_menu(name="VIEW3D_MT_edit_mesh_edges")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"WARNING"}, f"Could not open Edge menu: {exc}")
            return {"CANCELLED"}

    def execute(self, context):
        return self.invoke(context, None)


def _view_direction_is_axis_aligned(rv3d, tolerance=0.985):
    """True when the viewport is very close to a world axis view."""
    if Vector is None or rv3d is None:
        return True
    try:
        view_direction = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
        if view_direction.length == 0:
            return True
        view_direction.normalize()
        axes = (
            Vector((1.0, 0.0, 0.0)), Vector((-1.0, 0.0, 0.0)),
            Vector((0.0, 1.0, 0.0)), Vector((0.0, -1.0, 0.0)),
            Vector((0.0, 0.0, 1.0)), Vector((0.0, 0.0, -1.0)),
        )
        return max(abs(view_direction.dot(axis)) for axis in axes) >= tolerance
    except Exception:
        return True


def _auto_perspective_after_orbit(context):
    """Best-effort Auto Perspective support for NavTools' MMB+Alt snap workflow.

    When the viewport is snapped onto a view plane it should be orthographic. As
    soon as the user orbits away from that plane, it should return to perspective.
    This mirrors Blender's Auto Perspective behaviour and protects against sessions
    where the preference did not update cleanly from the panel.
    """
    settings = get_settings(context)
    if not settings or not getattr(settings, "use_auto_perspective", True):
        return
    area = getattr(context, "area", None)
    if not area or area.type != "VIEW_3D":
        return
    space = getattr(context, "space_data", None)
    rv3d = getattr(space, "region_3d", None)
    if not rv3d:
        return
    try:
        if rv3d.view_perspective == "ORTHO" and not _view_direction_is_axis_aligned(rv3d):
            rv3d.view_perspective = "PERSP"
    except Exception:
        pass


class NAVTOOLS_OT_hint_mouse_tracker(Operator):
    bl_idname = "navtools.hint_mouse_tracker"
    bl_label = "NavTools Hover Help Tracker"
    bl_description = "Passive mouse tracker for NavTools viewport hover descriptions"
    bl_options = set()

    def invoke(self, context, event):
        _hover_tool_state["running"] = True
        try:
            _hover_tool_state["mouse_x"] = int(getattr(event, "mouse_region_x", -10000))
            _hover_tool_state["mouse_y"] = int(getattr(event, "mouse_region_y", -10000))
            _hover_tool_state["mouse_window_x"] = int(getattr(event, "mouse_x", -10000))
            _hover_tool_state["mouse_window_y"] = int(getattr(event, "mouse_y", -10000))
            _hover_tool_state["last_move_at"] = time.monotonic()
        except Exception:
            pass
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        settings = get_settings(context)
        if not settings or not getattr(settings, "use_navtools", False):
            _hover_tool_state["running"] = False
            return {"CANCELLED"}

        try:
            if hasattr(event, "mouse_region_x") and hasattr(event, "mouse_region_y"):
                _hover_tool_state["mouse_x"] = int(event.mouse_region_x)
                _hover_tool_state["mouse_y"] = int(event.mouse_region_y)
            if hasattr(event, "mouse_x") and hasattr(event, "mouse_y"):
                _hover_tool_state["mouse_window_x"] = int(event.mouse_x)
                _hover_tool_state["mouse_window_y"] = int(event.mouse_y)
            _hover_tool_state["last_move_at"] = time.monotonic()
            _auto_perspective_after_orbit(context)
            if getattr(event, "type", "") in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
                _tag_view3d_redraw(context)
        except Exception:
            pass

        # Keep the scale readout visible while native Blender scale is active,
        # then clear it when the user explicitly confirms/cancels. A short grace
        # period avoids clearing from the key event that launched the transform.
        try:
            if _scale_readout_state.get("active"):
                etype = getattr(event, "type", "")
                evalue = getattr(event, "value", "")
                started = float(_scale_readout_state.get("started_at", 0.0) or 0.0)
                old_enough = (time.monotonic() - started) > 0.25
                if old_enough and evalue == "PRESS" and etype in {"LEFTMOUSE", "RET", "NUMPAD_ENTER", "SPACE", "RIGHTMOUSE", "ESC"}:
                    _reset_scale_readout()
                    _tag_view3d_redraw(context)
        except Exception:
            pass
        return {"PASS_THROUGH"}


# -----------------------------------------------------------------------------
# Menus
# -----------------------------------------------------------------------------

class NAVTOOLS_MT_view_pie(Menu):
    bl_label = "NavTools View Pie"
    bl_idname = "NAVTOOLS_MT_view_pie"

    def draw(self, context):
        pie = self.layout.menu_pie()
        op = pie.operator("navtools.view_axis", text="Left")
        op.axis = "LEFT"
        op = pie.operator("navtools.view_axis", text="Right")
        op.axis = "RIGHT"
        op = pie.operator("navtools.view_axis", text="Bottom")
        op.axis = "BOTTOM"
        op = pie.operator("navtools.view_axis", text="Top")
        op.axis = "TOP"
        op = pie.operator("navtools.view_axis", text="Front")
        op.axis = "FRONT"
        op = pie.operator("navtools.view_axis", text="Back")
        op.axis = "BACK"
        pie.operator("navtools.frame_selected_plus", text="Frame Selected")
        pie.operator("navtools.toggle_perspective_ortho", text="Persp / Ortho")


class NAVTOOLS_MT_scale_axis_pie(Menu):
    bl_label = "NavTools Scale Axis Pie"
    bl_idname = "NAVTOOLS_MT_scale_axis_pie"

    def draw(self, context):
        pie = self.layout.menu_pie()
        op = pie.operator("navtools.scale_transform", text="X only")
        op.axis = "X"
        op = pie.operator("navtools.scale_transform", text="Y only")
        op.axis = "Y"
        op = pie.operator("navtools.scale_transform", text="XY plane")
        op.axis = "XY"
        op = pie.operator("navtools.scale_transform", text="XZ plane")
        op.axis = "XZ"
        op = pie.operator("navtools.scale_transform", text="Z only")
        op.axis = "Z"
        op = pie.operator("navtools.scale_transform", text="YZ plane")
        op.axis = "YZ"
        op = pie.operator("navtools.scale_transform", text="Uniform")
        op.axis = "UNIFORM"
        op = pie.operator("navtools.set_transform_tool", text="Scale Gizmo")
        op.tool = "SCALE"



# -----------------------------------------------------------------------------
# Viewport hint overlay
# -----------------------------------------------------------------------------

def _has_object_selection(context):
    active = getattr(context, "active_object", None)
    selected = list(getattr(context, "selected_objects", []) or [])
    return bool(active or selected)


def _selected_quality_of_life_lines(settings):
    """Quality-of-life lines shown only when their features are selected."""
    lines = []
    if getattr(settings, "use_frame_selected_f", False):
        lines.append(("F = Frame Selected", "body"))
    if getattr(settings, "use_view_pie", False):
        lines.append(("Alt + Q = Maya-style View Pie", "body"))
    if getattr(settings, "show_native_snap_view_hint", True):
        mode = "Ortho, then auto-perspective" if getattr(settings, "use_auto_perspective", True) else "Perspective"
        lines.append((f"MMB orbit + Alt = Snap to Nearest View Plane ({mode})", "body"))
    if getattr(settings, "use_frame_all_shift_a", False):
        lines.append(("Shift + F = Frame All", "body"))
    if getattr(settings, "use_walk_rmb_shift", False):
        lines.append(("Shift + RMB = Unreal-style Navigation", "body"))
    return lines


def _selection_hint_lines(context):
    """Return structured hint rows: (text, style).

    Styles:
    - title: largest heading
    - section: group heading
    - body: normal shortcut line
    - note: smaller helper text
    - blank: vertical gap
    """
    settings = get_settings(context)
    if not settings:
        return []
    if not getattr(settings, "use_navtools", False) or not getattr(settings, "use_selection_hints", True):
        return []

    mode = getattr(context, "mode", "")
    edit_obj = getattr(context, "edit_object", None)

    # In Object Mode, only show when there is an actual selection.
    if mode == "OBJECT" and not _has_object_selection(context):
        return []

    # In Edit Mode Blender has an edit object selected, so show the edit-mode sheet.
    if mode.startswith("EDIT") and not edit_obj:
        return []

    is_edit = mode.startswith("EDIT")
    title = "NavTools: Edit Mode" if is_edit else "NavTools: Object Mode"
    if getattr(settings, "r_key_action", "SCALE_GIZMO") == "SCALE_GIZMO":
        scale_line = "R = Scale Gizmo"
    else:
        scale_line = "R = Scale"
    if getattr(settings, "use_snapped_scale_shift_r", False):
        scale_line += "        Shift + R = Increment Scale (10%)"

    lines = [
        (title, "title"),
        ("", "blank"),
        ("Navigation (Maya-style)", "section"),
        ("Alt + LMB = Orbit", "body"),
        ("Alt + MMB = Pan", "body"),
        ("Alt + RMB = Zoom", "body"),
        ("", "blank"),
        ("Transform Tools", "section"),
        ("W = Move", "body"),
        ("E = Rotate", "body"),
        (scale_line, "body"),
        ("X / Y / Z constrains the active transform to that World axis", "note"),
        ("XX / YY / ZZ constrains to the Local/object axis", "note"),
    ]

    if is_edit:
        shortcut_lines = []
        if getattr(settings, "use_shift_e_extrude", False):
            shortcut_lines.append(("Shift + E = Extrude", "body"))
        if getattr(settings, "use_ctrl_e_edge_menu", False):
            shortcut_lines.append(("Ctrl + E = Edge Menu", "body"))
        if getattr(settings, "use_ctrl_shift_e_edge_crease", False):
            shortcut_lines.append(("Ctrl + Shift + E = Edge Crease / original Shift + E", "body"))

        qol_lines = _selected_quality_of_life_lines(settings)
        if shortcut_lines or qol_lines:
            lines.extend([("", "blank"), ("Shortcuts", "section")])
            lines.extend(shortcut_lines)
            if shortcut_lines:
                lines.append(("Some keybindings have been remapped to make room for industry standard shortcuts.", "note"))
            if shortcut_lines and qol_lines:
                lines.append(("", "blank"))
            lines.extend(qol_lines)
    else:
        qol_lines = _selected_quality_of_life_lines(settings)
        if qol_lines:
            lines.extend([("", "blank"), ("Quality of Life Extras", "section")])
            lines.extend(qol_lines)

    return lines


def _tool_hover_description(text):
    """Return a short readable explanation for a hint row, or None if it is not a hoverable tool."""
    if not text:
        return None
    clean = " ".join(str(text).split())

    checks = [
        ("Alt + LMB = Orbit", "Orbit the 3D view using Maya-style navigation."),
        ("Alt + MMB = Pan", "Pan or track the 3D view without changing the view angle."),
        ("Alt + RMB = Zoom", "Zoom or dolly the 3D view in and out."),
        ("W = Move", "Move."),
        ("E = Rotate", "Rotate. In Mesh Edit Mode, Extrude has moved to Shift + E."),
        ("R = Scale", "Scale tool. Shift + R starts Blender's native snapped/increment scale."),
        ("X / Y / Z", "During a transform, X, Y and Z constrain to world axes. Double-tap the axis for local/object space."),
        ("XX / YY / ZZ", "During a transform, constrain in local/object space."),
        ("Shift + E = Extrude", "Extrude selected mesh components using Blender's native interactive Extrude + Move behaviour."),
        ("Ctrl + E = Edge Menu", "Open Blender's Edge menu for edge-specific mesh tools."),
        ("Ctrl + Shift + E", "Run Blender's original Shift + E / Edge Crease action."),
        ("F = Frame Selected", "Focus the viewport on the selected object or mesh components."),
        ("Shift + F = Frame All", "Frame all visible scene contents in the viewport."),
        ("Alt + Q", "Open a Maya-style view pie."),
        ("MMB orbit + Alt", "While middle-mouse orbiting, press Alt to snap to the nearest orthographic view plane."),
        ("Shift + RMB", "Unreal-style navigation: WASD movement with RMB orbiting."),
    ]
    for prefix, desc in checks:
        if clean.startswith(prefix):
            return desc
    return None


def _wrap_text_for_width(font_id, text, size, max_width):
    """Wrap text for a pixel width using BLF measurements."""
    text = str(text)
    words = text.split()
    if not words:
        return [""]
    lines = []
    current = words[0]
    for word in words[1:]:
        test = current + " " + word
        if _text_dimensions(font_id, test, size)[0] <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_hover_description_box(font_id, description, main_box, region_width, region_height, settings):
    """Draw a large description box beside the main hint panel."""
    if not description:
        return
    box_x, box_y, box_w, box_h = main_box
    margin = 18
    pad_x = 18
    pad_y = 16
    title_size = 30
    body_size = 26
    hover_w = int(getattr(settings, "hover_description_width", 460.0) or 460.0)
    hover_w = max(260, min(hover_w, max(260, region_width - margin * 2)))
    max_text_w = hover_w - pad_x * 2
    lines = _wrap_text_for_width(font_id, description, body_size, max_text_w)
    line_h = int(max(_text_dimensions(font_id, "Ag", body_size)[1], body_size))
    title_h = int(max(_text_dimensions(font_id, "NavTools", title_size)[1], title_size))
    hover_h = pad_y * 2 + title_h + 10 + len(lines) * (line_h + 5)

    # Prefer the right side of the hint box, otherwise place it on the left.
    if box_x + box_w + margin + hover_w <= region_width - margin:
        hx = box_x + box_w + margin
    else:
        hx = max(margin, box_x - hover_w - margin)
    hy = min(max(margin, box_y + box_h - hover_h), max(margin, region_height - hover_h - margin))

    _draw_transparent_rect(hx, hy, hover_w, hover_h, color=(0.02, 0.02, 0.02, 0.72))
    y = hy + hover_h - pad_y - title_h
    _draw_text_line(font_id, "Description", hx + pad_x, y, size=title_size, style="title")
    y -= title_h + 10
    for line in lines:
        _draw_text_line(font_id, line, hx + pad_x, y, size=body_size, style="body")
        y -= line_h + 5


def _hint_detail_description(context, settings):
    """Stable description text shown beside the shortcut panel; no hover required."""
    topic = getattr(settings, "hint_detail_topic", "AUTO")
    mode = getattr(context, "mode", "")
    if topic == "AUTO":
        topic = "SHORTCUTS" if str(mode).startswith("EDIT") else "OVERVIEW"

    if topic == "OVERVIEW":
        return (
            "NavTools gives Blender Maya-like navigation and industry-style transform controls across Object and Edit modes. "
            "Use the left panel as a quick reference while modelling."
        )
    if topic == "NAVIGATION":
        return (
            "Hold Alt with the mouse buttons to orbit, pan and zoom. You can still use Blender's native MMB orbit, then press Alt to snap to the nearest view plane."
        )
    if topic == "TRANSFORM":
        return (
            "Industry-style transform shortcuts using W, E and R. W = Move. E = Rotate. R = Scale tool. During a transform, press X, Y or Z to constrain to a world axis; double-tap the axis for local/object space."
        )
    if topic == "SCALE":
        return (
            "R starts Blender's native scale. Shift+R starts Blender's native snapped scale. "
            "NavTools does not override the snap amount, so Blender's own increment behaviour stays consistent."
        )
    if topic == "QOL":
        return (
            "Quality of Life Extras are optional viewport helpers such as Frame Selected, the Maya-style View Pie, frame all, walk/fly navigation and native snap-to-view hints."
        )
    if topic == "SHORTCUTS":
        return (
            "In Edit Mode, some keybindings have been remapped to make room for industry standard shortcuts. E rotates, Shift+E extrudes, Ctrl+E opens the Edge menu, and Ctrl+Shift+E keeps the original edge crease action."
        )
    return None


def _draw_hint_detail_box(font_id, context, settings, main_box, region_width, region_height):
    """Draw the reliable NavTools description box beside the main hints."""
    if not getattr(settings, "use_hint_detail_box", True):
        return
    description = _hint_detail_description(context, settings)
    if not description:
        return
    _draw_hover_description_box(font_id, description, main_box, region_width, region_height, settings)

def _hint_style_size(style):
    if style == "title":
        return 28
    if style == "section":
        return 20
    if style == "note":
        return 16
    if style == "blank":
        return 10
    return 18


def _hint_style_color(style):
    if style == "title":
        return (1.0, 1.0, 1.0, 0.96)
    if style == "section":
        return (0.92, 0.92, 0.92, 0.94)
    if style == "note":
        return (0.80, 0.80, 0.80, 0.90)
    return (1.0, 1.0, 1.0, 0.92)


def _set_blf_size(font_id, size):
    if blf is None:
        return
    try:
        blf.size(font_id, size)
    except TypeError:
        try:
            blf.size(font_id, size, 72)
        except Exception:
            pass


def _text_dimensions(font_id, text, size):
    _set_blf_size(font_id, size)
    try:
        return blf.dimensions(font_id, text)
    except Exception:
        return (len(text) * max(size * 0.55, 7), size)


def _draw_transparent_rect(x, y, w, h, color=(0.02, 0.02, 0.02, 0.62)):
    """Draw a translucent background rectangle behind the hint text."""
    if gpu is None or batch_for_shader is None:
        return False
    try:
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        vertices = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        indices = ((0, 1, 2), (0, 2, 3))
        batch = batch_for_shader(shader, "TRIS", {"pos": vertices}, indices=indices)
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
        gpu.state.blend_set("NONE")
        return True
    except Exception:
        try:
            gpu.state.blend_set("NONE")
        except Exception:
            pass
        return False


def _draw_text_line(font_id, text, x, y, size=15, style="body"):
    if blf is None or not text:
        return
    _set_blf_size(font_id, size)
    color = _hint_style_color(style)
    try:
        # Slight shadow keeps it readable over both light and dark geometry.
        blf.color(font_id, 0.0, 0.0, 0.0, 0.78)
        blf.position(font_id, x + 2, y - 2, 0)
        blf.draw(font_id, text)
        blf.color(font_id, color[0], color[1], color[2], color[3])
        blf.position(font_id, x, y, 0)
        blf.draw(font_id, text)
    except Exception:
        pass



def _selection_world_center_for_label(context):
    """Best-effort selected centre used for the small near-gizmo snap label."""
    mode = getattr(context, "mode", "")

    if mode.startswith("EDIT"):
        obj = getattr(context, "edit_object", None)
        if obj:
            if getattr(obj, "type", "") == "MESH" and bmesh is not None:
                try:
                    bm = bmesh.from_edit_mesh(obj.data)
                    verts = [v.co.copy() for v in bm.verts if v.select]
                    if verts:
                        local = sum(verts, Vector()) / len(verts)
                        return obj.matrix_world @ local
                except Exception:
                    pass
            try:
                return obj.matrix_world.translation.copy()
            except Exception:
                return None
        return None

    selected = list(getattr(context, "selected_objects", []) or [])
    if not selected:
        obj = getattr(context, "active_object", None)
        selected = [obj] if obj else []
    if not selected:
        return None

    try:
        total = Vector((0.0, 0.0, 0.0))
        count = 0
        for obj in selected:
            if not obj:
                continue
            total += obj.matrix_world.translation
            count += 1
        if count:
            return total / count
    except Exception:
        pass
    return None


def _draw_scale_snap_near_gizmo(context, settings):
    """Show a small fixed 10% snapped-scale readout while Shift+R scale is active."""
    if blf is None or location_3d_to_region_2d is None:
        return
    if not getattr(settings, "use_navtools", False):
        return
    # No user percentage option here: this is a fixed 10% guide, matching Blender's
    # reliable native increment snapping behaviour.
    if not getattr(settings, "use_scale_shortcuts", False) or not getattr(settings, "use_snapped_scale_shift_r", False):
        return

    center = _selection_world_center_for_label(context)
    if center is None:
        return

    region = getattr(context, "region", None)
    rv3d = getattr(getattr(context, "space_data", None), "region_3d", None)
    if not region or not rv3d:
        return

    try:
        coord = location_3d_to_region_2d(region, rv3d, center)
    except Exception:
        coord = None
    if coord is None:
        return

    width = getattr(region, "width", 0)
    height = getattr(region, "height", 0)
    if width <= 0 or height <= 0:
        return

    # Only show this label while NavTools snapped scale has been started via Shift+R.
    if not _scale_readout_state.get("active") or not _scale_readout_state.get("increment_snap"):
        return

    lines = ["Snap Scale: 10% increments"]
    scale_line = _format_scale_change_line(context)
    if scale_line:
        lines.append(scale_line)
    count_line = _format_scale_increment_count_line(context)
    if count_line:
        lines.append(count_line)

    font_id = 0
    size = 15
    line_gap = 5
    dims = [_text_dimensions(font_id, line, size) for line in lines]
    line_h = max(max(int(h), size) for _w, h in dims) if dims else size
    pad_x = 12
    pad_y = 8
    box_w = int(max((w for w, _h in dims), default=120) + pad_x * 2)
    box_h = int(len(lines) * line_h + max(0, len(lines) - 1) * line_gap + pad_y * 2)

    # Offset to the right of the selected centre so it sits beside the gizmo rather
    # than on top of it, then clamp to the viewport so it does not vanish off-screen.
    x = int(coord.x + 92)
    y = int(coord.y + 22)
    margin = 10
    x = max(margin, min(x, max(margin, width - box_w - margin)))
    y = max(margin, min(y, max(margin, height - box_h - margin)))

    _draw_transparent_rect(x, y, box_w, box_h, color=(0.02, 0.02, 0.02, 0.54))
    text_y = y + box_h - pad_y - line_h
    for index, line in enumerate(lines):
        style = "body" if index == 0 else "subtle"
        _draw_text_line(font_id, line, x + pad_x, text_y, size=size, style=style)
        text_y -= line_h + line_gap



def _wrap_hint_rows(rows, font_id, max_text_width):
    """Wrap long overlay rows so the hint panel keeps a readable width."""
    wrapped = []
    for text, style in rows:
        if style == "blank" or not text:
            wrapped.append((text, style))
            continue

        size = _hint_style_size(style)
        line_w, _line_h = _text_dimensions(font_id, text, size)
        if line_w <= max_text_width:
            wrapped.append((text, style))
            continue

        # Estimate characters-per-line from the measured text, then wrap at spaces.
        avg_char_w = max(line_w / max(len(text), 1), size * 0.45, 6.0)
        width_chars = max(24, int(max_text_width / avg_char_w))
        parts = textwrap.wrap(
            text,
            width=width_chars,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [text]

        for index, part in enumerate(parts):
            wrapped.append((part, style if index == 0 else "note"))

    return wrapped

def _navtools_hint_draw_callback():
    if blf is None:
        return
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if not area or area.type != "VIEW_3D" or not region or region.type != "WINDOW":
        return

    settings = get_settings(context)
    if not settings:
        return

    # Small near-gizmo snap percentage can be shown even if the larger cheat sheet is hidden.
    _draw_scale_snap_near_gizmo(context, settings)

    rows = _selection_hint_lines(context)
    if not rows:
        return

    font_id = 0
    width = getattr(region, "width", 0)
    height = getattr(region, "height", 0)

    pad_x = 18
    pad_y = 16
    margin = 20
    row_gap = 5

    configured_width = int(getattr(settings, "hint_overlay_width", 500.0) or 500.0)
    max_box_w = max(260, min(configured_width, width - margin * 2))
    max_text_w = max(180, max_box_w - pad_x * 2)
    rows = _wrap_hint_rows(rows, font_id, max_text_w)

    # Measure the overlay box after wrapping.
    max_w = 0
    total_h = 0
    measured = []
    for text, style in rows:
        size = _hint_style_size(style)
        if style == "blank":
            line_w, line_h = 0, size
        else:
            line_w, line_h = _text_dimensions(font_id, text, size)
        line_h = max(int(line_h), size)
        measured.append((text, style, size, line_w, line_h))
        max_w = max(max_w, min(line_w, max_text_w))
        total_h += line_h + row_gap
    total_h = max(0, total_h - row_gap)

    box_w = int(min(max_box_w, max_w + pad_x * 2))
    box_h = int(total_h + pad_y * 2)

    pos = getattr(settings, "hint_overlay_position", "BOTTOM_LEFT")
    if pos.endswith("RIGHT"):
        box_x = max(margin, width - box_w - margin)
    else:
        box_x = margin

    if pos.startswith("BOTTOM"):
        box_y = margin
    else:
        box_y = max(margin, height - box_h - margin)

    _draw_transparent_rect(box_x, box_y, box_w, box_h)

    # Draw from top to bottom inside the rectangle, and detect hover on tool rows.
    cursor_y = box_y + box_h - pad_y
    text_x = box_x + pad_x
    hover_description = None
    # The mouse tracker stores both region and window coordinates. Window
    # coordinates are more reliable when the tracker was started from the sidebar,
    # so convert them into this View3D region when possible.
    reg_x = int(getattr(region, "x", 0) or 0)
    reg_y = int(getattr(region, "y", 0) or 0)
    win_x = int(_hover_tool_state.get("mouse_window_x", -10000))
    win_y = int(_hover_tool_state.get("mouse_window_y", -10000))
    raw_region_x = int(_hover_tool_state.get("mouse_x", -10000))
    raw_region_y = int(_hover_tool_state.get("mouse_y", -10000))

    # Different Blender event contexts can report either window-space or
    # region-space mouse coordinates. Try both and choose the candidate that
    # lands inside this viewport. This makes the help box reliable even when
    # the tracker was started from the sidebar.
    candidates = []
    if win_x > -9999 and win_y > -9999:
        candidates.append((win_x - reg_x, win_y - reg_y))
    candidates.append((raw_region_x, raw_region_y))
    mouse_x, mouse_y = -10000, -10000
    for cand_x, cand_y in candidates:
        if 0 <= cand_x <= width and 0 <= cand_y <= height:
            mouse_x, mouse_y = cand_x, cand_y
            break
    if mouse_x == -10000:
        mouse_x, mouse_y = candidates[0]

    for text, style, size, _line_w, line_h in measured:
        if style == "blank":
            cursor_y -= line_h + row_gap
            continue
        draw_y = cursor_y - line_h
        _draw_text_line(font_id, text, text_x, draw_y, size=size, style=style)

        if getattr(settings, "use_hover_tool_descriptions", True):
            # Use the full panel row as the hit target, not just the exact text
            # width, so the help behaves like a proper tooltip panel.
            if box_x <= mouse_x <= box_x + box_w and draw_y - 6 <= mouse_y <= cursor_y + 6:
                hover_description = _tool_hover_description(text) or hover_description

        cursor_y -= line_h + row_gap

    # The old viewport description/hover box has been removed. Descriptions now
    # live in the sidebar using per-section info toggles, which is more reliable
    # and avoids obscuring the scene.

def _ensure_hint_draw_handler():
    global _hint_draw_handler
    if _hint_draw_handler is None:
        try:
            _hint_draw_handler = bpy.types.SpaceView3D.draw_handler_add(
                _navtools_hint_draw_callback, (), "WINDOW", "POST_PIXEL"
            )
        except Exception:
            _hint_draw_handler = None


def _remove_hint_draw_handler():
    global _hint_draw_handler
    if _hint_draw_handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_hint_draw_handler, "WINDOW")
        except Exception:
            pass
        _hint_draw_handler = None


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------



def _ui_wrapped_label(layout, text, icon="NONE", width=34):
    """Draw a long sidebar note as several labels so it wraps in the narrow N-panel."""
    parts = textwrap.wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False) or [str(text)]
    for index, part in enumerate(parts):
        if index == 0 and icon != "NONE":
            layout.label(text=part, icon=icon)
        else:
            layout.label(text=part)

def _draw_section(layout, settings, prop_name, title, icon="TRIA_RIGHT"):
    row = layout.row(align=True)
    expanded = getattr(settings, prop_name)
    row.prop(settings, prop_name, text="", icon="TRIA_DOWN" if expanded else "TRIA_RIGHT", emboss=False)
    row.label(text=title, icon=icon)
    if expanded:
        return layout.box()
    return None


def _draw_info_toggle(layout, settings, prop_name):
    """Draw a compact per-section info toggle using Blender's info icon."""
    row = layout.row(align=True)
    row.prop(settings, prop_name, text="Info", icon="INFO")
    return bool(getattr(settings, prop_name, False))


def _draw_section_description(layout, intro=None, tools=None, note=None):
    box = layout.box()
    if intro:
        _ui_wrapped_label(box, intro, icon="INFO", width=44)
    if tools:
        for shortcut, desc in tools:
            _description_tool(box, shortcut, desc)
    if note:
        _ui_wrapped_label(box, note, icon="ERROR", width=44)



def _description_tool(layout, shortcut, desc):
    row = layout.row(align=True)
    row.label(text=shortcut)
    _ui_wrapped_label(layout, desc, width=46)


def _draw_description_panel(layout, settings):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Description", icon="INFO")
    _ui_wrapped_label(box, "NavTools gives Blender Maya-like viewport navigation and W/E/R transform controls across Object and Edit modes.", width=48)

    col = box.column(align=True)
    col.label(text="Navigation", icon="VIEW3D")
    _description_tool(col, "Alt + LMB", "Orbit the 3D view.")
    _description_tool(col, "Alt + MMB", "Pan or track the 3D view.")
    _description_tool(col, "Alt + RMB", "Zoom or dolly the 3D view.")
    _description_tool(col, "Navigation Tweaks", "Select how Blender responds to navigation input.")

    col = box.column(align=True)
    col.separator()
    col.label(text="Quality of Life Extras", icon="OUTLINER_OB_LIGHTPROBE")
    _description_tool(col, "F", "Frame the selected object.")
    _description_tool(col, "Alt + Q", "Open a Maya-style view pie.")
    _description_tool(col, "MMB + Alt", "While middle-mouse orbiting, press Alt to snap to the nearest orthographic view plane.")
    _description_tool(col, "Shift + F", "Frame all visible scene contents when the option is enabled.")
    _description_tool(col, "Shift + RMB", "Unreal-style navigation: WASD movement with RMB orbiting.")

    col = box.column(align=True)
    col.separator()
    col.label(text="Transform Tools", icon="ORIENTATION_GLOBAL")
    _description_tool(col, "W", "Move.")
    _description_tool(col, "E", "Rotate. In Mesh Edit Mode, Extrude is moved to Shift + E.")
    _description_tool(col, "R", "Scale tool.")
    _description_tool(col, "X / Y / Z", "During a transform, constrain to a world axis.")
    _description_tool(col, "XX / YY / ZZ", "During a transform, constrain in local/object space.")

    col = box.column(align=True)
    col.separator()
    col.label(text="Helpers", icon="HELP")
    _ui_wrapped_label(col, "Options for users to customise.", width=48)
    _description_tool(col, "Show Hints", "Shows or hides tool hints.")
    _description_tool(col, "Hint Position", "Customise where hints appear.")
    _description_tool(col, "Scale Helpers", "Show scale helpers.")
    _description_tool(col, "Shift + R", "Increment Scale. Fixed 10% incremental scale.")

    col = box.column(align=True)
    col.separator()
    col.label(text="Modelling Shortcuts", icon="MODIFIER")
    _ui_wrapped_label(col, "Some keybindings have been remapped to make room for industry standard shortcuts.", width=48)
    _description_tool(col, "Shift + E", "Extrude selected mesh components using Blender's native interactive extrude behaviour.")
    _description_tool(col, "Ctrl + E", "Open Blender's Edge Menu.")
    _description_tool(col, "Ctrl + Shift + E", "Run Blender's original Shift + E / Edge Crease action.")


class NAVTOOLS_PT_tools(Panel):
    bl_label = "NavTools"
    bl_idname = "NAVTOOLS_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NavTools"

    def draw(self, context):
        layout = self.layout
        settings = get_settings(context)
        if not settings:
            layout.label(text="Settings unavailable", icon="ERROR")
            return

        if getattr(settings, "use_navtools", False) and getattr(settings, "use_hover_tool_descriptions", True):
            _ensure_hover_tracker(context)

        box = _draw_section(layout, settings, "show_status_section", "Status", "PREFERENCES")
        if box:
            if _draw_info_toggle(box, settings, "info_status"):
                _draw_section_description(
                    box,
                    intro="NavTools gives Blender Maya-like navigation and industry-style W/E/R transform controls across Object and Edit modes.",
                    tools=(
                        ("Install", "Edit > Preferences > Add-ons > Install, choose this Python file, enable the add-on, then open the 3D View sidebar with N and use the NavTools tab."),
                        ("Use", "Tick Enable NavTools to apply the Maya-style navigation and W/E/R transform layout. Untick it or use Restore Blender Defaults to switch back."),
                        ("Known limitation", "Shift + R uses Blender's native increment scale behaviour. NavTools shows a fixed 10% guide/counter, but the final snapping behaviour is still handled by Blender."),
                        ("Before uninstalling", "Use Restore Blender Defaults before disabling or uninstalling if you want to return to Blender's native controls."),
                    ),
                )
            row = box.row(align=True)
            row.prop(settings, "use_navtools", text="Enable NavTools")
            row.prop(settings, "use_selection_hints", text="Show Hints")
            if settings.use_navtools:
                box.label(text="Enabled", icon="CHECKMARK")
            else:
                box.label(text="Disabled", icon="CANCEL")

        box = _draw_section(layout, settings, "show_nav_extras_section", "Quality of Life Extras", "VIEW3D")
        if box:
            if _draw_info_toggle(box, settings, "info_qol"):
                _draw_section_description(
                    box,
                    intro="A collection of useful viewport extras. Select the features you want from here.",
                    tools=(
                        ("F", "Frame the selected object."),
                        ("Alt + Q", "Open a Maya-style view pie."),
                        ("MMB + Alt", "While middle-mouse orbiting, press Alt to snap to the nearest orthographic view plane."),
                        ("Shift + F", "Frame all visible scene contents when the option is enabled."),
                        ("Shift + RMB", "Unreal-style navigation: WASD movement with RMB orbiting."),
                    ),
                )
            box.prop(settings, "use_frame_selected_f")
            box.prop(settings, "use_view_pie")
            box.prop(settings, "show_native_snap_view_hint", text="MMB + Alt = Snap to Nearest Orthographic View Plane")
            sub = box.row(align=True)
            sub.enabled = settings.show_native_snap_view_hint
            sub.prop(settings, "use_auto_perspective", text="Snap result: Ortho / Auto Perspective")
            box.prop(settings, "use_frame_all_shift_a")
            box.prop(settings, "use_walk_rmb_shift")

        box = _draw_section(layout, settings, "show_native_tweaks_section", "Navigation Tweaks", "TOOL_SETTINGS")
        if box:
            if _draw_info_toggle(box, settings, "info_nav_tweaks"):
                _draw_section_description(
                    box,
                    intro="Select how Blender responds to navigation input.",
                    tools=(
                        ("Orbit Around Selection", "Orbit around the selected object instead of an unrelated view centre."),
                        ("Zoom to Mouse Position", "Zoom towards the area under the mouse cursor."),
                        ("Depth Navigation", "Uses scene depth under the cursor to make navigation feel more grounded around visible geometry."),
                    ),
                )
            box.prop(settings, "use_rotate_around_selection")
            box.prop(settings, "use_zoom_to_mouse")
            box.prop(settings, "use_mouse_depth_navigation")
            box.operator("navtools.restore_nav_tweaks", text="Restore Navigation Tweaks")

        box = _draw_section(layout, settings, "show_transform_section", "Transform Keys", "ORIENTATION_GLOBAL")
        if box:
            if _draw_info_toggle(box, settings, "info_transform"):
                _draw_section_description(
                    box,
                    intro="Industry-style transform shortcuts using W, E and R.",
                    tools=(
                        ("W", "Move."),
                        ("E", "Rotate. In Mesh Edit Mode, Extrude is moved to Shift + E."),
                        ("R", "Scale tool."),
                        ("X / Y / Z", "During a transform, constrain to a world axis."),
                        ("XX / YY / ZZ", "During a transform, constrain in local/object space."),
                    ),
                )
            box.prop(settings, "use_transform_gizmo_keys")
            box.prop(settings, "transform_key_scope")
            box.prop(settings, "r_key_action")
            box.prop(settings, "scale_tool_mode")
            box.prop(settings, "enable_gizmo_visibility_on_tool_change")

        box = _draw_section(layout, settings, "show_scale_section", "Helpers", "HELP")
        if box:
            if _draw_info_toggle(box, settings, "info_scale"):
                _draw_section_description(
                    box,
                    intro="Options for users to customise.",
                    tools=(
                        ("Show Hints", "Shows or hides tool hints."),
                        ("Hint Position", "Customise where hints appear."),
                        ("Scale Helpers", "Show scale helpers."),
                        ("Shift + R", "Increment Scale. Fixed 10% incremental scale."),
                    ),
                )
            box.prop(settings, "use_selection_hints", text="Show On-screen Hints")
            hint_col = box.column()
            hint_col.enabled = settings.use_selection_hints
            hint_col.prop(settings, "hint_overlay_position")
            box.separator()
            box.prop(settings, "use_scale_shortcuts")
            scale_col = box.column()
            scale_col.enabled = settings.use_scale_shortcuts
            scale_col.prop(settings, "use_snapped_scale_shift_r")

        box = _draw_section(layout, settings, "show_modeling_section", "Modelling Shortcuts", "MODIFIER")
        if box:
            if _draw_info_toggle(box, settings, "info_modeling"):
                _draw_section_description(
                    box,
                    intro="Some keybindings have been remapped to make room for industry standard shortcuts.",
                    tools=(
                        ("Shift + E", "Extrude selected mesh components using Blender's native interactive extrude behaviour."),
                        ("Ctrl + E", "Open Blender's Edge Menu."),
                        ("Ctrl + Shift + E", "Run Blender's original Shift + E / Edge Crease action."),
                    ),
                )
            box.prop(settings, "use_modeling_shortcuts")
            col = box.column()
            col.enabled = settings.use_modeling_shortcuts
            col.prop(settings, "use_shift_e_extrude")
            col.prop(settings, "use_ctrl_shift_e_edge_crease")
            col.prop(settings, "use_ctrl_e_edge_menu")

        box = _draw_section(layout, settings, "show_actions_section", "Restore", "FILE_REFRESH")
        if box:
            if _draw_info_toggle(box, settings, "info_actions"):
                _draw_section_description(
                    box,
                    intro="Restore either NavTools' recommended settings or Blender's normal behaviour.",
                    tools=(
                        ("Restore NavTools Defaults", "Resets NavTools' own settings to the recommended layout."),
                        ("Restore Blender Defaults", "Removes NavTools keymaps and restores native Blender behaviour changed this session. Use this before disabling or uninstalling if you want to switch back cleanly."),
                    ),
                )
            box.operator("navtools.restore_navtools_defaults", text="Restore NavTools Defaults", icon="FILE_REFRESH")
            box.operator("navtools.restore_blender_defaults", text="Restore Blender Defaults", icon="LOOP_BACK")


classes = (
    NAVTOOLS_PG_settings,
    NAVTOOLS_OT_enable_tools,
    NAVTOOLS_OT_restore_navtools_defaults,
    NAVTOOLS_OT_restore_blender_defaults,
    NAVTOOLS_OT_apply_nav_tweaks,
    NAVTOOLS_OT_restore_nav_tweaks,
    NAVTOOLS_OT_frame_selected_plus,
    NAVTOOLS_OT_view_all_plus,
    NAVTOOLS_OT_toggle_perspective_ortho,
    NAVTOOLS_OT_view_axis,
    NAVTOOLS_OT_snap_nearest_ortho,
    NAVTOOLS_OT_set_transform_tool,
    NAVTOOLS_OT_scale_transform,
    NAVTOOLS_OT_show_scale_axis_pie,
    NAVTOOLS_OT_extrude_region_plus,
    NAVTOOLS_OT_edge_crease_plus,
    NAVTOOLS_OT_show_edge_menu,
    NAVTOOLS_OT_hint_mouse_tracker,
    NAVTOOLS_MT_view_pie,
    NAVTOOLS_MT_scale_axis_pie,
    NAVTOOLS_PT_tools,
)


def _migrate_legacy_scale_snap_value():
    # Legacy no-op: scale snapping is now native-only.
    pass

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.navtools_settings = PointerProperty(type=NAVTOOLS_PG_settings)
    _ensure_hint_draw_handler()
    try:
        _ensure_hover_tracker(bpy.context)
    except Exception:
        pass


def unregister():
    try:
        remove_navtools_keymaps()
    except Exception:
        pass
    try:
        _restore_input_prefs()
    except Exception:
        pass
    try:
        _remove_hint_draw_handler()
    except Exception:
        pass
    if hasattr(bpy.types.WindowManager, "navtools_settings"):
        del bpy.types.WindowManager.navtools_settings
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
