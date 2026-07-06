# NavTools

**NavTools** is a lightweight Blender add-on created by **Dave Wilson** for artists who prefer industry-style viewport navigation and W/E/R transform controls.

It is designed to make Blender feel more familiar for artists coming from Maya-style or studio-style 3D workflows. One click gives you familiar Alt-mouse navigation, W/E/R transform keys, helpful viewport extras and remapped edit-mode shortcuts. It is just as easy to switch back to Blender defaults.

If you enjoy what I do, consider supporting me! Every little bit means a lot! https://ko-fi.com/ubudave

---

## Features

### Industry-style navigation

- **Alt + Left Mouse** = Orbit
- **Alt + Middle Mouse** = Pan
- **Alt + Right Mouse** = Zoom
- **Middle Mouse orbit + Alt** = Snap to nearest orthographic view plane

### Transform controls

- **W** = Move
- **E** = Rotate
- **R** = Scale tool
- **X / Y / Z** = Constrain transform to world axis
- **XX / YY / ZZ** = Constrain transform to local/object axis

### Quality of life extras

- **F** = Frame selected object
- **Alt + Q** = Maya-style View Pie
- **Shift + F** = Frame all visible scene contents
- **Shift + RMB** = Unreal-style navigation / WASD movement with RMB orbiting

### Edit Mode shortcut remapping

Some keybindings have been remapped to make room for industry standard shortcuts.

- **Shift + E** = Extrude selected mesh components
- **Ctrl + E** = Edge Menu
- **Ctrl + Shift + E** = Original Shift + E / Edge Crease action

---

## Installation

1. Download the latest `navtools_vxxx.py` file from the Releases page.
2. Open Blender.
3. Go to **Edit > Preferences > Add-ons**.
4. Click **Install**.
5. Select the downloaded `navtools_vxxx.py` file.
6. Enable the add-on.
7. Open the 3D View sidebar with **N**.
8. Go to the **NavTools** tab.
9. Tick **Enable NavTools**.

---

## Switching back to Blender defaults

To return to Blender’s normal navigation and shortcut behaviour:

1. Open the **NavTools** panel.
2. Press **Restore Blender Defaults**.
3. Disable or remove the add-on afterwards if required.

For best results, use **Restore Blender Defaults** before uninstalling or disabling NavTools.

---

## Known limitations

- **Shift + R** uses Blender’s native increment scale behaviour. NavTools displays a fixed 10% guide, but the final snapping behaviour is still handled by Blender.
- Some shortcuts may conflict with custom Blender keymaps or third-party add-ons.
- Graphics driver overlays or system-level shortcuts may intercept some key combinations before Blender receives them.
- NavTools has currently been tested primarily as a workflow helper for artists using standard Blender viewport and modelling tools.

---

## Feedback and bug reports

Feedback is welcome.

When reporting a bug, please include:

- Blender version
- Operating system
- Whether you use the default Blender keymap
- Other navigation or keymap add-ons installed
- What you expected to happen
- What actually happened
- Screenshots or short videos if useful

Please use GitHub Issues for bugs and feature requests.

---

## Planned future ideas

Possible future improvements include:

- Presets for different navigation styles
- More context related information on screen to aid modelling
- Being able to set scale increments
- Optional Unreal-style navigation refinements, Extending the functionality allowing users to switch between control methods easily
- Additional transform and pivot helpers, custom helpers more in line with Maya/Max functionality
- Import/export of NavTools settings

---

## Licence

NavTools is released under the **GNU General Public License v3.0 or later**.

See the `LICENSE` file for details.

---

## Credits

Created by **Dave Wilson**.

Built with assistance from AI.
