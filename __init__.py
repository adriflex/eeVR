""" Initialize eeVR blender plugin """

# pylint: disable=import-error

import os
import time
from datetime import datetime
from math import radians, degrees

if "bpy" in locals():
    import importlib
    importlib.reload(renderer)
else:
    from . import renderer

from .renderer import Renderer

import bpy
from bpy.types import Context, Operator, Panel

bl_info = {
    "name": "eeVR",
    "description": "Render in different projections using Eevee engine",
    "author": "EternalTrail",
    "version": (1, 0, 1),
    "blender": (3, 6, 0),
    "location": "Properties > Render Tab (Available when EEVEE or Workbench)",
    "wiki_url": "https://github.com/EternalTrail/eeVR",
    "tracker_url": "https://github.com/EternalTrail/eeVR/issues",
    "support": "COMMUNITY",
    "category": "Render",
}


def has_invalid_condition(self : 'Operator', context : 'Context'):
    if context.scene.camera == None:
        self.report({'ERROR'}, "eeVR ERROR : Scene camera is not set.")
        return True
    if context.active_object != None and context.active_object.mode != 'OBJECT':
        self.report({'ERROR'}, "eeVR ERROR : Active object's mode is not object mode.")
        return True
    return False


class RenderImage(Operator):
    """Render out the animation"""

    bl_idname = 'eevr.render_image'
    bl_label = "Render a single frame"

    def execute(self, context):
        print("eeVR: execute")

        if has_invalid_condition(self, context):
            return {'FINISHED'}

        renderer = Renderer(context, False)
        now = time.time()
        try:
            renderer.render_and_save()
        finally:
            renderer.clean_up(context)

        print(f"eeVR: {round(time.time() - now, 2)} seconds")

        return {'FINISHED'}


class RenderAnimation(Operator):
    """Render out the animation"""

    bl_idname = 'eevr.render_animation'
    bl_label = "Render the animation"

    def __del__(self):
        print("eeVR: end")

    def modal(self, context, event):
        if event.type in {'ESC'}:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            wm = context.window_manager
            wm.event_timer_remove(self.timer)

            if context.scene.eeVR.cancel:
                self.cancel(context)
                return {'CANCELLED'}

            if context.scene.frame_current <= self.frame_end:
                print(f"eeVR: Rendering frame {context.scene.frame_current}")
                now = time.time()
                try:
                    self.renderer.render_and_save()
                except Exception as e:
                    self.clean(context)
                    raise e
                print(f"eeVR: {round(time.time() - now, 2)} seconds")
                self.timer = wm.event_timer_add(0.1, window=context.window)
            else:
                self.clean(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def execute(self, context):
        print("eeVR: execute")

        if has_invalid_condition(self, context):
            return {'FINISHED'}

        context.scene.eeVR.cancel = False

        # knowing it's animation, creates folder outside vrrender class, pass folder name to it
        start_time = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        folder_name = f"{os.path.splitext(bpy.path.basename(bpy.data.filepath))[0]} {start_time}"
        path = bpy.path.abspath(context.preferences.filepaths.render_output_directory)
        if not path: path = bpy.path.abspath("//")
        full_path = os.path.join(path, folder_name)
        os.makedirs(full_path, exist_ok=True)
        self.renderer = Renderer(context, True, folder_name)

        self.frame_end = context.scene.frame_end
        frame_start = context.scene.frame_start
        context.scene.frame_set(frame_start)
        wm = context.window_manager
        self.timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        print("eeVR: cancel")
        self.clean(context)

    def clean(self, context):
        self.renderer.clean_up(context)
        context.scene.eeVR.cancel = True


class Cancel(Operator):
    """Render out the animation"""

    bl_idname = 'eevr.render_cancel'
    bl_label = "Cancel the render"

    def execute(self, context):
        context.scene.eeVR.cancel = True
        return {'FINISHED'}


class GenerateCompositorTemplate(Operator):
    """Generate a compositor node tree for stitching"""

    bl_idname = 'eevr.generate_compositor_template'
    bl_label = "Generate Compositor Template"

    def execute(self, context):
        from .renderer import Renderer, setup_compositor_template

        # Initialize renderer to get the correct resolution and shader
        # Use folder name for template
        start_time = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        folder_name = f"template_{start_time}"
        path = bpy.path.abspath(context.preferences.filepaths.render_output_directory)
        if not path: path = bpy.path.abspath("//")
        os.makedirs(os.path.join(path, folder_name), exist_ok=True)

        r = Renderer(context, False, folder_name)
        try:
            uv_maps = r.generate_uv_maps()
            setup_compositor_template(context, uv_maps)
            self.report({'INFO'}, f"Template generated in {folder_name}")
        finally:
            r.clean_up(context)

        return {'FINISHED'}


class StitchFolder(Operator):
    """Stitch all frames in a folder"""

    bl_idname = 'eevr.stitch_folder'
    bl_label = "Stitch Folder"

    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .renderer import Renderer
        import re

        if not self.directory:
            self.report({'ERROR'}, "No directory selected")
            return {'CANCELLED'}

        files = os.listdir(self.directory)
        # Group files by frame number
        # Pattern: frame_(\d+)_(front|back|left|right|top|bottom)(_L|_R)?\.(png|exr|tga|...)
        pattern = re.compile(r"frame_(\d+)_(\w+)(_L|_R)?\..*")

        frames = {}
        for f in files:
            match = pattern.match(f)
            if match:
                frame_num = match.group(1)
                direction = match.group(2)
                eye = match.group(3) or ""

                if frame_num not in frames:
                    frames[frame_num] = {}
                if eye not in frames[frame_num]:
                    frames[frame_num][eye] = {}

                frames[frame_num][eye][direction] = os.path.join(self.directory, f)

        if not frames:
            self.report({'ERROR'}, "No matching face images found in folder")
            return {'CANCELLED'}

        r = Renderer(context, True, "stitched/")
        os.makedirs(os.path.join(r.path, r.folder_name), exist_ok=True)

        try:
            for frame_num in sorted(frames.keys()):
                self.report({'INFO'}, f"Stitching frame {frame_num}")

                eye_images = {}
                for eye in frames[frame_num]:
                    # Build image list in correct order for cubemap_to_panorama
                    # Order: front, [left, right], [bottom, top], [back]
                    dirs = ['front']
                    if not r.no_side_images: dirs += ['left', 'right']
                    if not r.no_top_bottom_images: dirs += ['bottom', 'top']
                    if not r.no_back_image: dirs += ['back']

                    img_list = []
                    for d in dirs:
                        if d in frames[frame_num][eye]:
                            img_path = frames[frame_num][eye][d]
                            try:
                                img = bpy.data.images.load(img_path)
                                img_list.append(img)
                            except:
                                self.report({'ERROR'}, f"Failed to load {img_path}")
                                img = bpy.data.images.new(f"error_{d}", 8, 8)
                                img_list.append(img)
                        else:
                            self.report({'WARNING'}, f"Missing direction {d} for frame {frame_num} {eye}")
                            # Add a placeholder black image to maintain list order
                            img = bpy.data.images.new(f"missing_{d}", 8, 8)
                            img_list.append(img)

                    eye_images[eye] = img_list

                # Call stitching logic
                # This is a bit tricky because render_and_save is designed for active rendering
                # I'll need a way to just do the stitch part

                # For now, let's manually do what render_and_save does but with our images
                if r.is_stereo:
                    left_list = eye_images.get("_L", [])
                    right_list = eye_images.get("_R", [])
                    # ... (Need to handle the stereo combine logic)
                    # Actually, let's use a helper in Renderer
                    r.stitch_and_save_custom(left_list, right_list, frame_num)
                else:
                    mono_list = eye_images.get("", [])
                    r.stitch_and_save_custom(mono_list, None, frame_num)

        finally:
            r.clean_up(context)

        return {'FINISHED'}


class RenderPanel(Panel):
    """Render panel for VR rendering"""

    bl_idname = "EEVR_PT_render"
    bl_label = "eeVR"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "eeVR"

    COMPAT_ENGINES = {'BLENDER_RENDER', 'BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT', 'BLENDER_WORKBENCH'}

    @classmethod
    def poll(cls, context):
        return (context.engine in cls.COMPAT_ENGINES)

    def draw(self, context):

        props : Properties = context.scene.eeVR
        # Draw the buttons for each of the rendering operators
        layout = self.layout
        col = layout.column()
        col.prop(props, 'renderModeEnum')
        if props.renderModeEnum == 'DOME':
            col.prop(props, 'domeMethodEnum')
        col.prop(props, 'fovModeEnum')
        if props.fovModeEnum == '180':
            col.prop(props, 'HFOV180')
        else:
            col.prop(props, 'HFOV360')
        col.prop(props, 'VFOV')
        if props.get_hfov() <= radians(270):
            col.prop(props, 'frontFOV')
            max_fov = props.get_max_fov()
            if props.frontFOV > max_fov:
                col.label(icon='ERROR', text=f"clips to {degrees(max_fov):3.0f}°")
        col.prop(props, 'stitchMargin')
        col.prop(props, 'frontViewResolution')
        if props.get_hfov() > props.get_front_fov():
            col.prop(props, 'sideViewResolution')
        if props.get_vfov() > props.get_front_fov():
            col.prop(props, 'topViewResolution')
            col.prop(props, 'bottomViewResolution')
        if props.get_hfov() > radians(270):
            col.prop(props, 'rearViewResolution')
        if context.scene.render.use_multiview and props.is_top_bottom(context):
            col.prop(props, 'isTopRightEye')
        if context.scene.render.use_multiview and props.get_hfov() > radians(180):
            col.prop(props, 'appliesParallaxForSideAndBack')
            col.label(icon='ERROR', text="eeVR cannot support stereo over 180° fov correctly.")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Optimization & Control:")
        col.prop(props, 'saveIntermediate')
        if props.saveIntermediate:
            col.prop(props, 'intermediateSubdir')
            col.prop(props, 'skipStitching')

        layout.separator()
        col = layout.column(align=True)
        col.operator(GenerateCompositorTemplate.bl_idname, icon='NODE_COMPOSITING')
        col.operator(StitchFolder.bl_idname, icon='FILE_FOLDER')

        layout.separator()
        col = layout.column()
        col.operator(RenderImage.bl_idname, icon="RENDER_STILL", text="Render Image")
        col.operator(RenderAnimation.bl_idname, icon="RENDER_ANIMATION", text="Render Animation")
        if not props.cancel:
            col.operator(Cancel.bl_idname, text="Cancel")
            col.label(text=f"Rendering frame {context.scene.frame_current}")


# eeVR's properties
class Properties(bpy.types.PropertyGroup):

    renderModeEnum: bpy.props.EnumProperty(
        items=[
            ("EQUI", "Equirectangular", "Renders in equirectangular projection"),
            ("DOME", "Full Dome", "Renders in full dome projection"),
        ],
        default="EQUI",
        name="Mode",
    )

    domeMethodEnum: bpy.props.EnumProperty(
        items=[
            ("0", "Equidistant (VTA)", "Renders in equidistant dome projection"),
            ("1", "Hemispherical (VTH)", "Renders in hemispherical dome projection"),
            ("2", "Equisolid", "Renders in equisolid dome projection"),
            ("3", "Stereographic", "Renders in Stereographic dome projection"),
        ],
        default="0",
        name="Method",
    )

    fovModeEnum: bpy.props.EnumProperty(
        items=[
            ("180", "180°", "VR 180"),
            ("360", "360°", "VR 360 (over 180°, not support stereo)"),
            ("ANY", "Custom FOV", "over 180°, not support stereo"),
        ],
        default="180",
        name="VR Format",
    )

    HFOV360: bpy.props.FloatProperty(
        name="Horizontal FOV",
        subtype='ANGLE',
        precision=0,
        step=100,
        default=radians(360),
        min=radians(1),
        max=radians(360),
        description="Horizontal Field of view in degrees",
    )

    HFOV180: bpy.props.FloatProperty(
        name="Horizontal FOV",
        subtype='ANGLE',
        unit='ROTATION',
        precision=0,
        step=100,
        default=radians(180),
        min=radians(1),
        max=radians(180),
        description="Horizontal Field of view in degrees",
    )

    VFOV: bpy.props.FloatProperty(
        name="Vertical FOV",
        subtype='ANGLE',
        unit='ROTATION',
        precision=0,
        step=100,
        default=radians(180),
        min=radians(1),
        max=radians(180),
        description="Vertical Field of view in degrees",
    )

    frontFOV: bpy.props.FloatProperty(
        name="Front View FOV",
        subtype='ANGLE',
        unit='ROTATION',
        precision=0,
        step=100,
        default=radians(90),
        min=radians(90),
        max=radians(160),
        description="Front View's Field of view in degrees",
    )

    stitchMargin: bpy.props.FloatProperty(
        name="Stitch Margin",
        subtype='ANGLE',
        unit='ROTATION',
        precision=0,
        step=100,
        default=radians(5),
        min=radians(0),
        max=radians(15),
        description="Margin for Seam Blending in degrees",
    )

    frontViewResolution: bpy.props.FloatProperty(
        name="Front View Resolution",
        subtype='PERCENTAGE',
        precision=0,
        step=100,
        default=90,
        min=1,
        max=100,
        description="Overscan/Reduction Rate for Front View Rendering",
    )

    sideViewResolution: bpy.props.FloatProperty(
        name="Side View Resolution",
        subtype='PERCENTAGE',
        precision=0,
        step=100,
        default=90,
        min=1,
        max=100,
        description="Overscan/Reduction Rate for Side View Rendering",
    )

    topViewResolution: bpy.props.FloatProperty(
        name="Top View Resolution",
        subtype='PERCENTAGE',
        precision=0,
        step=100,
        default=90,
        min=1,
        max=100,
        description="Overscan/Reduction Rate for Top View Rendering",
    )

    bottomViewResolution: bpy.props.FloatProperty(
        name="Bottom View Resolution",
        subtype='PERCENTAGE',
        precision=0,
        step=100,
        default=90,
        min=1,
        max=100,
        description="Overscan/Reduction Rate for Bottom View Rendering",
    )

    rearViewResolution: bpy.props.FloatProperty(
        name="Rear View Resolution",
        subtype='PERCENTAGE',
        precision=0,
        step=100,
        default=90,
        min=1,
        max=100,
        description="Overscan/Reduction Rate for Rear View Rendering",
    )

    appliesParallaxForSideAndBack: bpy.props.BoolProperty(
        description="If it is on, it allows for noticeable seams or blending artifacts in side and rear views to introduce collect parallax"\
         " at over HFOV 180 rendering. default is false.",
        default=False,
        name="Apply Parallax for side and rear view",
    )

    isTopRightEye: bpy.props.BoolProperty(
        description="If it is on, right eye image will be placed as top image. default is false.",
        default=False,
        name="Top is RightEye",
    )

    trueTopBottom: bpy.props.BoolProperty(
        name="TrueTopBottom",
        default=False
    )

    cancel: bpy.props.BoolProperty(
        name="Cancel",
        default=True
    )

    saveIntermediate: bpy.props.BoolProperty(
        name="Save Intermediate Faces",
        description="Save the individual face images (front, back, etc.) to a sub-folder",
        default=False
    )

    skipStitching: bpy.props.BoolProperty(
        name="Skip Stitching",
        description="Only render the individual faces and skip the final panorama creation. Useful for very high resolutions",
        default=False
    )

    intermediateSubdir: bpy.props.StringProperty(
        name="Sub-folder Name",
        description="Name of the sub-folder where intermediate faces will be saved",
        default="faces"
    )

    @staticmethod
    def snap_angle(src: float) -> float:
        if abs(src - radians(90)) < 0.000001:
            return radians(90)
        elif abs(src - radians(180)) < 0.000001:
            return radians(180)
        elif abs(src - radians(270)) < 0.000001:
            return radians(270)
        elif abs(src - radians(360)) < 0.000001:
            return radians(360)
        return src

    def get_hfov(self) -> float:
        return self.snap_angle(self.HFOV180 if self.fovModeEnum == '180' else self.HFOV360)

    def get_vfov(self) -> float:
        return self.snap_angle(self.VFOV)

    def get_max_fov(self) -> float:
        return max(self.get_hfov(), self.get_vfov())

    def get_front_fov(self) -> float:
        return min(self.snap_angle(self.frontFOV), self.get_max_fov()) if self.get_hfov() <= radians(270) else radians(90)

    def is_top_bottom(self, context: Context) -> bool:
        if self.cancel:
            return context.scene.render.image_settings.stereo_3d_format.display_mode =='TOPBOTTOM'
        else:
            return self.trueTopBottom

    @classmethod
    def register(cls):
        """ Register eeVR's properties to Blender """
        bpy.types.Scene.eeVR = bpy.props.PointerProperty(type=cls)

    @classmethod
    def unregister(cls):
        """ Unregister eeVR's properties from Blender """
        del bpy.types.Scene.eeVR


class Preferences(bpy.types.AddonPreferences):
    # this must match the addon name, use '__package__'
    # when defining this in a submodule of a python package.
    bl_idname = __name__

    remain_temporalies: bpy.props.BoolProperty(
        name='Remain temporal work files',
        description='Temporal rendering images will not be removed.',
        default=False
    )

    temporal_file_format: bpy.props.EnumProperty(
        items=[
            ("PNG", "PNG", "Output image in PNG format."),
            ("TARGA_RAW", "Targa Raw", "Output image in uncompressed Targa format."),
        ],
        default="TARGA_RAW",
        name="Temporal File Format",
    )

    def draw(self, context):
        self.layout.row().prop(self, 'remain_temporalies')
        self.layout.row().prop(self, 'temporal_file_format')


# REGISTER
register, unregister = bpy.utils.register_classes_factory((
    Properties,
    RenderPanel,
    RenderImage,
    RenderAnimation,
    Cancel,
    GenerateCompositorTemplate,
    StitchFolder,
    Preferences,
))
