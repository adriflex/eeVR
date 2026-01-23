import bpy
import os
import time
import gpu
import numpy as np
from math import sin, cos, tan, ceil, degrees, pi
from datetime import datetime
from gpu_extras.batch import batch_for_shader
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from . import Properties
    from . import Preferences

# Define parts of fragment shader
commdef = '''
#define PI          3.1415926535897932384626
#define FOVFRAC     %f
#define SIDEFRAC    %f
#define TBFRAC      %f
#define HCLIP       %f
#define VCLIP       %f
#define HMARGIN     %f
#define VMARGIN     %f
#define SMARGIN     %f
#define EXTRUSION   %f
#define INTRUSION   %f

const float SIDEHTEXSCALE = 1.0 / max(SIDEFRAC - INTRUSION, 0.0001);
const float SIDEVTEXSCALE = 1.0 / max(1.0 + 2.0 * SMARGIN, 0.0001);
const float TBVTEXSCALE   = 1.0 / max(TBFRAC - INTRUSION, 0.0001);
const float HTEXSCALE     = 1.0 / max(1.0 + 2.0 * (HMARGIN + EXTRUSION), 0.0001);
const float VTEXSCALE     = 1.0 / max(1.0 + 2.0 * (VMARGIN + EXTRUSION), 0.0001);
const float ACTUALHMARGIN = HMARGIN * HTEXSCALE;
const float ACTUALVMARGIN = VMARGIN * VTEXSCALE;

vec2 tr(vec2 src, vec2 offset, vec2 scale)
{
    return (src + offset) * scale;
}

vec2 to_uv(float x, float y)
{
    return vec2(0.5 * x + 0.5, 0.5 * y + 0.5);
}

vec2 to_uv_right(vec3 pt)
{
    return tr(to_uv(-pt.z/pt.x, pt.y/pt.x), vec2(-INTRUSION, SMARGIN), vec2(SIDEHTEXSCALE, SIDEVTEXSCALE));
}

vec2 to_uv_left(vec3 pt)
{
    return tr(to_uv(-pt.z/pt.x, -pt.y/pt.x), vec2(SIDEFRAC - 1, SMARGIN), vec2(SIDEHTEXSCALE, SIDEVTEXSCALE));
}

vec2 to_uv_top(vec3 pt)
{
    return tr(to_uv(pt.x/pt.y, -pt.z/pt.y), vec2(0, -INTRUSION), vec2(1, TBVTEXSCALE));
}

vec2 to_uv_bottom(vec3 pt)
{
    return tr(to_uv(-pt.x/pt.y, -pt.z/pt.y), vec2(0, TBFRAC - 1), vec2(1, TBVTEXSCALE));
}

vec2 apply_margin(vec2 src)
{
    return tr(src, vec2(HMARGIN + EXTRUSION, VMARGIN + EXTRUSION), vec2(HTEXSCALE, VTEXSCALE));
}

vec2 to_uv_front(vec3 pt)
{
    return apply_margin(to_uv(pt.x/pt.z, pt.y/pt.z));
}

vec2 to_uv_back(vec3 pt)
{
    return apply_margin(to_uv(pt.x/pt.z, -pt.y/pt.z));
}

float atan2(float y, float x)
{
    return x == 0.0 ? sign(y) * 0.5 * PI : atan(y, x);
}

void main() {
'''

dome = '''
    vec2 d = vTexCoord.xy;
    float r = length(d);
    if(r > 1.0) discard;

    // Calculate the position on unit sphere
    vec2 dunit = normalize(d);
    float phi = FOVFRAC * PI * r;
    vec3 pt;
    %s
    pt.z = cos(phi);
    float azimuth = atan2(pt.x, pt.z);
'''

domemodes = [
    'pt.xy = dunit * phi;',
    'pt.xy = dunit * sin(phi);',
    'pt.xy = 2.0 * dunit * sin(phi * 0.5);',
    'pt.xy = 2.0 * dunit * tan(phi * 0.5);'
]

equi = '''
    // Calculate the pointing angle
    float azimuth = FOVFRAC * PI * vTexCoord.x;
    float elevation = 0.5 * PI * vTexCoord.y;

    // Calculate the position on unit sphere
    vec3 pt;
    pt.x = cos(elevation) * sin(azimuth);
    pt.y = sin(elevation);
    pt.z = cos(elevation) * cos(azimuth);
'''

fetch_setup = '''
    // Select the correct pixel
    // left or right
    float lor = step(abs(pt.y), abs(pt.x)) * step(abs(pt.z), abs(pt.x));
    // top or bottom
    float tob = (1 - lor) * step(abs(pt.z), abs(pt.y));
    // front or back
    float fob = (1 - lor) * (1 - tob);
    float right = step(0, pt.x);
    float up = step(0, pt.y);
    float front = step(0, pt.z);
    float over45 = step(0.25 * PI, abs(azimuth));
    float over135 = step(0.75 * PI, abs(azimuth));
    {
        float angle;
        angle = (fob + tob) * (1 - over45) * front * abs(pt.x/pt.z) * 0.25 * PI;
        angle += (lor + tob) * over45 * (1 - over135) * right * (2 - pt.z/pt.x) * 0.25 * PI;
        angle += (lor + tob) * over45 * (1 - over135) * (1 - right) * (pt.z/pt.x + 2) * 0.25 * PI;
        angle += (fob + tob) * over135 * (1 - front) * (4 - abs(pt.x/pt.z)) * 0.25 * PI;
        if(angle > HCLIP*0.5) discard;
    }
    {
        float near_horizon = step(abs(pt.y), abs(pt.z));
        float angle;
        angle = (fob + lor * near_horizon) * abs(pt.y/pt.z) * 0.25 * PI;
        angle += (tob + lor * (1 - near_horizon)) * (2 - abs(pt.z/pt.y)) * 0.25 * PI;
        if(angle > VCLIP*0.5) discard;
    }
    fragColor = vec4(0.0);
'''

fetch_top_bottom = '''
    fragColor += tob * up * texture(cubeTopImage, to_uv_top(pt));
    fragColor += tob * (1 - up) * texture(cubeBottomImage, to_uv_bottom(pt));
'''

fetch_sides = '''
    vec2 right_uv = to_uv_right(pt);
    float right_inner = step(0, right_uv.x);
    fragColor += lor * right * right_inner * texture(cubeRightImage, right_uv);
    vec2 left_uv = to_uv_left(pt);
    float left_inner = step(left_uv.x, 1);
    fragColor += lor * (1 - right) * left_inner * texture(cubeLeftImage, left_uv);
'''

blend_seam_sides = '''
    {
        float range = over45 * (1 - over135);

        float alpha = range * right * tob * up * smoothstep(1.0, 0.0, clamp((right_uv.y - 1 + ACTUALVMARGIN) / ACTUALVMARGIN, 0.0, 1.0));
        alpha *= right_inner;
        fragColor = mix(fragColor, texture(cubeRightImage, right_uv), alpha);

        alpha = range * right * tob * (1 - up) * smoothstep(0.0, 1.0, clamp(right_uv.y / ACTUALVMARGIN, 0.0, 1.0));
        alpha *= right_inner;
        fragColor = mix(fragColor, texture(cubeRightImage, right_uv), alpha);

        alpha = range * (1 - right) * tob * up * smoothstep(1.0, 0.0, clamp((left_uv.y - 1 + ACTUALVMARGIN) / ACTUALVMARGIN, 0.0, 1.0));
        alpha *= left_inner;
        fragColor = mix(fragColor, texture(cubeLeftImage, left_uv), alpha);

        alpha = range * (1 - right) * tob * (1 - up) * smoothstep(0.0, 1.0, clamp(left_uv.y / ACTUALVMARGIN, 0.0, 1.0));
        alpha *= left_inner;
        fragColor = mix(fragColor, texture(cubeLeftImage, left_uv), alpha);
    }
'''

fetch_back = '''
    {
        vec2 uv = to_uv_back(pt);
        fragColor += fob * (1 - front) * texture(cubeBackImage, uv);
%s
    }
'''

fetch_front = '''
    {
        vec2 uv = to_uv_front(pt);
        fragColor += fob * front * texture(cubeFrontImage, uv);
%s
    }
'''

blend_seam_front_h = '''
        {
            float in_range = step(0, uv.x) * (1 - step(1, uv.x));
            float alpha = in_range * front * lor * right * smoothstep(1.0, 0.0, clamp((uv.x - 1 + ACTUALHMARGIN) / ACTUALHMARGIN, 0.0, 1.0));
            fragColor = mix(fragColor, texture(cubeFrontImage, uv), alpha);

            alpha = in_range * front * lor * (1 - right) * smoothstep(0.0, 1.0, clamp(uv.x / ACTUALHMARGIN, 0.0, 1.0));
            fragColor = mix(fragColor, texture(cubeFrontImage, uv), alpha);
        }
'''

blend_seam_front_v = '''
        {
            float in_range = step(0, uv.y) * (1 - step(1, uv.y));
            float alpha = in_range * front * tob * up * smoothstep(1.0, 0.0, clamp((uv.y - 1 + ACTUALVMARGIN) / ACTUALVMARGIN, 0.0, 1.0));
            fragColor = mix(fragColor, texture(cubeFrontImage, uv), alpha);

            alpha = in_range * front * tob * (1 - up) * smoothstep(0.0, 1.0, clamp(uv.y / ACTUALVMARGIN, 0.0, 1.0));
            fragColor = mix(fragColor, texture(cubeFrontImage, uv), alpha);
        }
'''

blend_seam_back_h = '''
        {
            float alpha = (1 - front) * lor * right * smoothstep(1.0, 0.0, clamp((1.0 - uv.x - 1 + ACTUALHMARGIN) / ACTUALHMARGIN, 0.0, 1.0));
            fragColor = mix(fragColor, texture(cubeBackImage, uv), alpha);

            alpha = (1 - front) * lor * (1 - right) * smoothstep(0.0, 1.0, clamp((1.0 - uv.x) / ACTUALHMARGIN, 0.0, 1.0));
            fragColor = mix(fragColor, texture(cubeBackImage, uv), alpha);
        }
'''

blend_seam_back_v = '''
        {
            float alpha = (1 - front) * tob * up * smoothstep(1.0, 0.0, clamp((uv.y - 1 + ACTUALVMARGIN) / ACTUALVMARGIN, 0.0, 1.0));
            fragColor = mix(fragColor, texture(cubeBackImage, uv), alpha);

            alpha = (1 - front) * tob * (1 - up) * smoothstep(0.0, 1.0, clamp(uv.y / ACTUALVMARGIN, 0.0, 1.0));
            fragColor = mix(fragColor, texture(cubeBackImage, uv), alpha);
        }
'''

# Define the vertex shader
vertex_shader = '''
void main() {
    vTexCoord = aVertexTextureCoord;
    gl_Position = vec4(aVertexPosition, 1);
}
'''

class Renderer:

    def __init__(self, context : bpy.types.Context, is_animation = False):

        # Check if the file is saved or not, can cause errors when not saved
        if not bpy.data.is_saved:
            raise PermissionError("Save file before rendering")

        props: Properties = context.scene.eeVR
        addon_id = __package__ if __package__ else __name__.partition('.')[0]
        self.preferences: Preferences = context.preferences.addons[addon_id].preferences

        # Set internal variables for the class
        self.scene = context.scene
        # Get the file extension and format from render settings
        self.fformat = self.scene.render.image_settings.file_format
        self.color_mode = self.scene.render.image_settings.color_mode
        self.is_float = True if self.fformat in ['CINEON', 'DPX', 'OPEN_EXR_MULTILAYER', 'OPEN_EXR', 'HDR'] else False
        self.has_alpha = True if self.color_mode == 'RGBA' else False

        # save original active object
        self.viewlayer_active_object_origin = context.view_layer.objects.active
        # save original active camera handle
        self.camera_origin = context.scene.camera
        # create a new camera for rendering
        bpy.ops.object.camera_add()
        if context.object:
            self.camera = context.object
        else:
            # camera_add in render contect don't set context.object by new camera...
            if context.active_object and context.active_object.type == 'CAMERA':
                self.camera = context.active_object
            else:
                raise PermissionError("Script cannot handle added temporal camera")
        self.camera.name = 'eeVR_camera'
        # set new cam active
        context.scene.camera = self.camera
        # set coordinates same as origin
        self.camera.matrix_world = self.camera_origin.matrix_world
        # transfer key attributes
        self.camera.data.stereo.interocular_distance = self.camera_origin.data.stereo.interocular_distance
        self.camera.data.clip_start = self.camera_origin.data.clip_start
        self.camera.data.clip_end = self.camera_origin.data.clip_end

        # Output directory from render settings
        self.output_filepath = bpy.path.abspath(self.scene.render.filepath)
        if not self.output_filepath:
            self.output_filepath = bpy.path.abspath("//")

        self.tmpdir = bpy.path.abspath(context.preferences.filepaths.temporary_directory if context.preferences.filepaths.temporary_directory else
                                       bpy.app.tempdir)

        # Temporal files settings
        self.tmpfile_format = self.preferences.temporal_file_format
        self.tmpfext = '.exr' if self.tmpfile_format == 'OPEN_EXR' else '.png' if self.tmpfile_format == 'PNG' else '.tga'

        self.is_stereo = self.scene.render.use_multiview
        self.is_animation = is_animation
        is_dome = props.renderModeEnum == 'DOME'
        h_fov = props.get_hfov()
        v_fov = props.get_vfov()
        front_fov = props.get_front_fov()
        ext_front_view = front_fov > pi/2
        self.no_back_image = h_fov <= 3*pi/2
        self.no_side_images = h_fov <= front_fov
        self.no_top_bottom_images = v_fov <= front_fov
        self.seamless = not (self.scene.render.use_multiview and h_fov > pi and props.appliesParallaxForSideAndBack)

        self.createdFiles = set()

        # Calcurate dimension
        self.resolution_x_origin = self.scene.render.resolution_x
        self.resolution_y_origin = self.scene.render.resolution_y
        self.pixel_aspect_x_origin = self.scene.render.pixel_aspect_x
        self.pixel_aspect_y_origin = self.scene.render.pixel_aspect_y
        self.resolution_percentage_origin = self.scene.render.resolution_percentage

        scale = self.resolution_percentage_origin / 100.0
        self.image_size = int(ceil(self.scene.render.resolution_x * scale)), int(ceil(self.scene.render.resolution_y * scale))

        coeff = 1 / sin(pi/4)
        if props.fovModeEnum == '180':
            resolution_rate = (0.5 * coeff, 0.5 * coeff)
        elif props.fovModeEnum == '360':
            resolution_rate = (0.25 * coeff, (0.25 if is_dome else 0.5) * coeff)
        elif is_dome:
            resolution_rate = ((pi/2) / max(h_fov, v_fov) * coeff, (pi/2) / max(h_fov, v_fov) * coeff)
        else:
            resolution_rate = ((pi/2) / h_fov * coeff, (pi/2) / v_fov * coeff)

        base_resolution = (
            int(ceil(self.image_size[0] * resolution_rate[0])),
            int(ceil(self.image_size[1] * resolution_rate[1]))
        )

        # Generate fragment shader code
        fovfrac = 0.5 if props.fovModeEnum == '180' else 1 if props.fovModeEnum == '360' else max(h_fov, v_fov) / (2*pi)
        sidefrac = max(0, min(1, (h_fov - pi/2) / pi))
        tbfrac = max(sidefrac, max(0, min(1, (v_fov - pi/2) / pi)))

        base_angle = min(h_fov, front_fov)

        stitch_margin = 0.0 if self.no_side_images and self.no_top_bottom_images else props.stitchMargin
        margin = max(0.0, 0.5 * (tan(base_angle/2 + stitch_margin) - tan(base_angle/2)))
        extrusion = max(0.0, 0.5 * tan(base_angle/2) - 0.5) if ext_front_view else 0.0
        intrusion = max(0.0, 0.5 - 0.5 * tan(pi/2-base_angle/2)) if ext_front_view else 0.0
        if tbfrac - intrusion <= 0.0 or base_resolution[1]*(tbfrac-intrusion) < 1.0:
            self.no_top_bottom_images = True
        hmargin = 0.0 if self.no_side_images else margin
        vmargin = 0.0 if self.no_top_bottom_images else margin
        smargin = 0.0 if self.no_side_images or not vmargin > 0.0 else max(0.0, 0.5 * (tan(pi/4 + stitch_margin) - tan(pi/4)))

        frag_shader = \
           (commdef % (fovfrac, sidefrac, tbfrac, h_fov, v_fov, hmargin, vmargin, smargin, extrusion, intrusion))\
         + (dome % domemodes[int(props.domeMethodEnum)] if is_dome else equi)\
         + fetch_setup\
         + ('' if self.no_top_bottom_images else fetch_top_bottom)\
         + ('' if self.no_side_images else fetch_sides + (blend_seam_sides if vmargin > 0.0 else ''))\
         + ('' if self.no_back_image else (fetch_back % ((blend_seam_back_h if hmargin > 0.0 else '') + (blend_seam_back_v if vmargin > 0.0 else ''))))\
         + (fetch_front % ((blend_seam_front_h if hmargin > 0.0 or ext_front_view else '') + (blend_seam_front_v if vmargin > 0.0 or ext_front_view else '')))\
         + '\n    fragColor.a = 1.0;\n}'

        shader_info = gpu.types.GPUShaderCreateInfo()
        vert_out = gpu.types.GPUStageInterfaceInfo("eevr")
        vert_out.smooth("VEC2", "vTexCoord")
        shader_info.vertex_in(0, 'VEC3', "aVertexPosition")
        shader_info.vertex_in(1, 'VEC2', "aVertexTextureCoord")
        shader_info.vertex_out(vert_out)
        shader_info.sampler(0, 'FLOAT_2D', "cubeLeftImage")
        shader_info.sampler(1, 'FLOAT_2D', "cubeRightImage")
        shader_info.sampler(2, 'FLOAT_2D', "cubeBottomImage")
        shader_info.sampler(3, 'FLOAT_2D', "cubeTopImage")
        shader_info.sampler(4, 'FLOAT_2D', "cubeBackImage")
        shader_info.sampler(5, 'FLOAT_2D', "cubeFrontImage")
        shader_info.fragment_out(0, 'VEC4', "fragColor")
        shader_info.vertex_source(vertex_shader)
        shader_info.fragment_source(frag_shader)
        self.shader = gpu.shader.create_from_info(shader_info)

        # Intermediate faces settings
        self.save_intermediate = self.preferences.remain_temporalies
        self.skip_stitching = props.skipStitching
        if self.save_intermediate:
            render_dir = os.path.dirname(self.output_filepath)
            self.intermediate_path = os.path.join(render_dir, props.intermediateSubdir)
            os.makedirs(self.intermediate_path, exist_ok=True)

        # Get initial camera and output information
        self.camera_rotation = list(self.camera.rotation_euler)
        self.IPD = self.camera.data.stereo.interocular_distance

        # Set camera variables for proper result
        self.camera.data.type = 'PERSP' # Use PERSP for multi-pass rendering to avoid Eevee's native pano issues
        self.camera.data.stereo.convergence_mode = 'PARALLEL'
        self.camera.data.stereo.pivot = 'CENTER'
        # transfer depth of field settings
        self.camera.data.dof.use_dof = self.camera_origin.data.dof.use_dof
        if self.camera.data.dof.use_dof:
            self.camera.data.dof.focus_distance = self.camera_origin.data.dof.focus_distance
            self.camera.data.dof.aperture_fstop = self.camera_origin.data.dof.aperture_fstop
            self.camera.data.dof.aperture_blades = self.camera_origin.data.dof.aperture_blades
            self.camera.data.dof.aperture_ratio = self.camera_origin.data.dof.aperture_ratio
            self.camera.data.dof.aperture_rotation = self.camera_origin.data.dof.aperture_rotation

        # setup render targets information
        aspect_ratio = base_resolution[0] / base_resolution[1]

        def make_resolution(hscale, vscale, hmargin, vmargin, scale):
            return int(ceil((base_resolution[0] * hscale + 2 * hmargin * base_resolution[0]) * scale)),\
                int(ceil((base_resolution[1] * vscale + 2 * vmargin * base_resolution[1]) * scale))

        f_resolution = make_resolution(1, 1, extrusion+hmargin, extrusion+vmargin, props.frontViewResolution / 100.0)
        b_resolution = make_resolution(1, 1, extrusion+hmargin, extrusion+vmargin, props.rearViewResolution / 100.0)
        side_resolution = make_resolution(sidefrac-intrusion, 1, 0, smargin, props.sideViewResolution / 100.0)
        top_resolution = make_resolution(1, tbfrac-intrusion, 0, 0, props.topViewResolution / 100.0)
        bot_resolution = make_resolution(1, tbfrac-intrusion, 0, 0, props.bottomViewResolution / 100.0)

        fb_angle = base_angle + 2 * stitch_margin
        side_angle = pi/2 + ((2 * stitch_margin) if smargin > 0.0 else 0.0)
        side_shift_scale = 1 / (1 + 2 * smargin)

        self.camera_settings = {
            'top': (0.0, 0.5*(tbfrac-1+intrusion), pi/2, top_resolution[0], top_resolution[1], aspect_ratio),
            'bottom': (0.0, 0.5*(1-tbfrac-intrusion), pi/2, bot_resolution[0], bot_resolution[1], aspect_ratio),
            'right': (0.5*(sidefrac-1+intrusion)*side_shift_scale, 0.0, side_angle, side_resolution[0], side_resolution[1], aspect_ratio),
            'left': (0.5*(1-sidefrac-intrusion)*side_shift_scale, 0.0, side_angle, side_resolution[0], side_resolution[1], aspect_ratio),
            'front': (0.0, 0.0, fb_angle, f_resolution[0], f_resolution[1], aspect_ratio),
            'back': (0.0, 0.0, fb_angle, b_resolution[0], b_resolution[1], aspect_ratio)
        }

        if self.is_stereo:
            self.view_format = self.scene.render.image_settings.views_format
            self.scene.render.image_settings.views_format = 'STEREO_3D'
            self.stereo_mode = self.scene.render.image_settings.stereo_3d_format.display_mode
            if self.stereo_mode in ('SIDEBYSIDE', 'TOPBOTTOM'):
                self.sidebyside = self.stereo_mode == 'SIDEBYSIDE'
            else:
                self.sidebyside = aspect_ratio < 1.5
            props.trueTopBottom = self.stereo_mode == 'TOPBOTTOM'
            self.use_sidebyside_crosseyed = self.scene.render.image_settings.stereo_3d_format.use_sidebyside_crosseyed
            self.scene.render.image_settings.stereo_3d_format.display_mode = 'TOPBOTTOM'

        self.direction_offsets = self.find_direction_offsets()


    def cubemap_to_panorama(self, imageList, outputName):

        # Generate the OpenGL shader
        pos = [(-1.0, -1.0, -1.0),  # left,  bottom, back
               (-1.0,  1.0, -1.0),  # left,  top,    back
               (1.0, -1.0, -1.0),   # right, bottom, back
               (1.0,  1.0, -1.0)]   # right, top,    back
        coords = [(-1.0, -1.0),  # left,  bottom
                  (-1.0,  1.0),  # left,  top
                  (1.0, -1.0),   # right, bottom
                  (1.0,  1.0)]   # right, top
        vertexIndices = [(0, 3, 1),(3, 0, 2)]

        batch = batch_for_shader(self.shader, 'TRIS', {
            "aVertexPosition": pos,
            "aVertexTextureCoord": coords
        }, indices=vertexIndices)

        # Change the color space of all of the images to Linear
        # and load them into OpenGL textures
        textures = []
        for image in imageList:
            image.colorspace_settings.name = 'Linear' if bpy.app.version < (4, 0, 0) else 'Linear Rec.709'
            image.update() # Ensure pixel data is synchronized
            tex = gpu.texture.from_image(image)
            textures.append(tex)

        # set the size of the final image
        width = self.image_size[0]
        height = self.image_size[1]

        # Create an offscreen render buffer and texture
        offscreen = gpu.types.GPUOffScreen(width, height)

        with offscreen.bind():
            fb = offscreen.framebuffer if hasattr(offscreen, 'framebuffer') else gpu.state.active_framebuffer_get()
            gpu.state.viewport_set(0, 0, width, height)
            fb.clear(color=(0.0, 0.0, 0.0, 0.0))
            self.shader.bind()

            self.shader.uniform_sampler("cubeFrontImage", textures[0])
            if self.no_side_images:
                if not self.no_top_bottom_images:
                    self.shader.uniform_sampler("cubeBottomImage", textures[1])
                    self.shader.uniform_sampler("cubeTopImage", textures[2])
                    if not self.no_back_image:
                        self.shader.uniform_sampler("cubeBackImage", textures[3])
                else:
                    if not self.no_back_image:
                        self.shader.uniform_sampler("cubeBackImage", textures[1])
            else:
                self.shader.uniform_sampler("cubeLeftImage", textures[1])
                self.shader.uniform_sampler("cubeRightImage", textures[2])
                if not self.no_top_bottom_images:
                    self.shader.uniform_sampler("cubeBottomImage", textures[3])
                    self.shader.uniform_sampler("cubeTopImage", textures[4])
                    if not self.no_back_image:
                        self.shader.uniform_sampler("cubeBackImage", textures[5])
                else:
                    if not self.no_back_image:
                        self.shader.uniform_sampler("cubeBackImage", textures[3])

            # Render the image
            batch.draw(self.shader)

            # Read the resulting pixels into a buffer
            # Omit data argument for 5.0 compatibility
            buffer = fb.read_color(0, 0, width, height, 4, 0, 'FLOAT')

        # Unload the offscreen texture
        offscreen.free()

        # Remove the cubemap textures:
        del textures
        for image in imageList:
            bpy.data.images.remove(image)

        # Copy the pixels from the buffer to an image object
        if not outputName in bpy.data.images.keys():
            bpy.data.images.new(outputName, width, height, float_buffer=self.is_float, alpha=self.has_alpha)
        imageRes = bpy.data.images[outputName]
        imageRes.file_format = self.fformat
        imageRes.scale(width, height)
        # Use to_list() for reliability in 5.0 if needed, but buffer might work
        try:
            imageRes.pixels.foreach_set(buffer.to_list())
        except:
            imageRes.pixels.foreach_set(buffer)
        imageRes.update()
        return imageRes


    def find_direction_offsets(self):

        # update location and rotation of our camera from origin one
        self.camera.matrix_world = self.camera_origin.matrix_world
        # Calculate the pointing directions of the camera for each face of the cube
        eul = self.camera.rotation_euler.copy()
        direction_offsets = {}
        #front
        direction_offsets['front'] = list(eul)
        #back
        eul.rotate_axis('Y', pi)
        direction_offsets['back'] = list(eul)
        #top
        eul = self.camera.rotation_euler.copy()
        eul.rotate_axis('X', pi/2)
        direction_offsets['top'] = list(eul)
        #bottom
        eul.rotate_axis('X', pi)
        direction_offsets['bottom'] = list(eul)
        #left
        eul = self.camera.rotation_euler.copy()
        eul.rotate_axis('Y', pi/2)
        direction_offsets['left'] = list(eul)
        #right
        eul.rotate_axis('Y', pi)
        direction_offsets['right'] = list(eul)
        return direction_offsets


    def set_camera_direction(self, direction):

        # Set the camera to the required postion
        self.camera.rotation_euler = self.direction_offsets[direction]
        self.camera.data.shift_x = self.camera_settings[direction][0]
        self.camera.data.shift_y = self.camera_settings[direction][1]
        self.camera.data.angle = self.camera_settings[direction][2]
        if self.camera.data.dof.use_dof:
            rate = tan(self.camera_origin.data.angle / 2) / tan(self.camera.data.angle / 2)
            self.camera.data.dof.aperture_fstop = self.camera_origin.data.dof.aperture_fstop * rate
        self.scene.render.resolution_x = self.camera_settings[direction][3]
        self.scene.render.resolution_y = self.camera_settings[direction][4]
        if self.camera_settings[direction][5] >= 1.0:
            self.scene.render.pixel_aspect_x = 1.0
            self.scene.render.pixel_aspect_y = self.camera_settings[direction][5]
        else:
            self.scene.render.pixel_aspect_x = 1 / self.camera_settings[direction][5]
            self.scene.render.pixel_aspect_y = 1.0
        self.scene.render.resolution_percentage = 100
        print(f"{direction} float:{self.is_float} alpha:{self.has_alpha} : {self.scene.render.resolution_x} x {self.scene.render.resolution_y} {degrees(self.camera.data.angle):.2f}° [{self.camera.data.shift_x:.3f}, {self.camera.data.shift_y:.3f}] ({self.scene.render.pixel_aspect_x:.2f} : {self.scene.render.pixel_aspect_y:.2f}) fstop={self.camera.data.dof.aperture_fstop:.2f}")


    def clean_up(self, context):

        # Reset all the variables that were changed
        context.view_layer.objects.active = self.viewlayer_active_object_origin
        context.scene.camera = self.camera_origin
        camera = self.camera.data
        bpy.data.objects.remove(self.camera)
        bpy.data.cameras.remove(camera)
        self.scene.render.resolution_x = self.resolution_x_origin
        self.scene.render.resolution_y = self.resolution_y_origin
        self.scene.render.pixel_aspect_x = self.pixel_aspect_x_origin
        self.scene.render.pixel_aspect_y = self.pixel_aspect_y_origin
        self.scene.render.resolution_percentage = self.resolution_percentage_origin
        if self.is_stereo:
            self.scene.render.image_settings.views_format = self.view_format
            self.scene.render.image_settings.stereo_3d_format.display_mode = self.stereo_mode
        if not self.preferences.remain_temporalies:
            for filename in self.createdFiles:
                try:
                    os.remove(filename)
                except Exception as e:
                    print('at remove temporary file.', e)
        self.createdFiles.clear()


    def render_image(self, direction):

        # Render the image and load it into the script
        name = f'temp_img_store_{os.getpid()}_{direction}'
        org_filepath = self.scene.render.filepath
        org_file_format = self.scene.render.image_settings.file_format
        org_color_depth = self.scene.render.image_settings.color_depth
        org_exr_codec = self.scene.render.image_settings.exr_codec

        # Use temporal settings
        self.scene.render.image_settings.file_format = self.tmpfile_format
        if self.tmpfile_format == 'OPEN_EXR':
            self.scene.render.image_settings.color_depth = '32' if self.is_float else '16'
            self.scene.render.image_settings.exr_codec = 'DWAA'

        frame_num = self.scene.frame_current
        # Base name for intermediate faces
        render_basename = os.path.splitext(os.path.basename(org_filepath))[0]
        if not render_basename: render_basename = "render"
        face_name_base = f"{render_basename}_{frame_num:04d}_{direction}"

        if self.is_stereo:
            nameL = name + '_L'
            nameR = name + '_R'
            if nameL in bpy.data.images:
                bpy.data.images.remove(bpy.data.images[nameL])
            if nameR in bpy.data.images:
                bpy.data.images.remove(bpy.data.images[nameR])

            if self.seamless and direction in {'right', 'left'}:
                # Separate eye rendering to avoid seams
                self.scene.render.use_multiview = False
                tmp_loc = list(self.camera.location)
                camera_angle = self.direction_offsets['front'][2]
                self.camera.location = [tmp_loc[0]+(0.5*self.IPD*cos(camera_angle)),\
                                        tmp_loc[1]+(0.5*self.IPD*sin(camera_angle)),\
                                        tmp_loc[2]]

                if self.save_intermediate:
                    pathL = os.path.join(self.intermediate_path, face_name_base + "_L" + self.tmpfext)
                else:
                    pathL = os.path.join(self.tmpdir, nameL + self.tmpfext)

                self.scene.render.filepath = pathL
                bpy.ops.render.render(write_still=True)
                if not self.save_intermediate:
                    self.createdFiles.add(pathL)

                renderedImageL = bpy.data.images.load(pathL)
                renderedImageL.name = nameL

                self.camera.location = [tmp_loc[0]-(0.5*self.IPD*cos(camera_angle)),\
                                        tmp_loc[1]-(0.5*self.IPD*sin(camera_angle)),\
                                        tmp_loc[2]]

                if self.save_intermediate:
                    pathR = os.path.join(self.intermediate_path, face_name_base + "_R" + self.tmpfext)
                else:
                    pathR = os.path.join(self.tmpdir, nameR + self.tmpfext)

                self.scene.render.filepath = pathR
                bpy.ops.render.render(write_still=True)
                if not self.save_intermediate:
                    self.createdFiles.add(pathR)

                renderedImageR = bpy.data.images.load(pathR)
                renderedImageR.name = nameR

                self.scene.render.use_multiview = True
                self.camera.location = tmp_loc

            else:
                pathMono = os.path.join(self.tmpdir, name + self.tmpfext)
                self.scene.render.filepath = pathMono
                bpy.ops.render.render(write_still=True)
                self.createdFiles.add(pathMono)

                renderedImage = bpy.data.images.load(pathMono)
                renderedImage.name = name
                renderedImage.colorspace_settings.name = 'Linear' if bpy.app.version < (4, 0, 0) else 'Linear Rec.709'
                renderedImage.update()

                # Split the render into two images
                imageLen = len(renderedImage.pixels)
                renderedImageL = bpy.data.images.new(nameL, self.scene.render.resolution_x, self.scene.render.resolution_y, float_buffer=self.is_float, alpha=self.has_alpha)
                renderedImageR = bpy.data.images.new(nameR, self.scene.render.resolution_x, self.scene.render.resolution_y, float_buffer=self.is_float, alpha=self.has_alpha)

                buff = np.empty((imageLen,), dtype=np.float32)
                renderedImage.pixels.foreach_get(buff)
                if self.seamless and direction == 'back':
                    renderedImageL.pixels.foreach_set(buff[imageLen//2:])
                    renderedImageR.pixels.foreach_set(buff[:imageLen//2])
                else:
                    renderedImageR.pixels.foreach_set(buff[imageLen//2:])
                    renderedImageL.pixels.foreach_set(buff[:imageLen//2])

                renderedImageL.update()
                renderedImageR.update()

                if self.save_intermediate:
                    renderedImageL.file_format = self.tmpfile_format
                    renderedImageL.filepath_raw = os.path.join(self.intermediate_path, face_name_base + "_L" + self.tmpfext)
                    renderedImageL.save()
                    renderedImageR.file_format = self.tmpfile_format
                    renderedImageR.filepath_raw = os.path.join(self.intermediate_path, face_name_base + "_R" + self.tmpfext)
                    renderedImageR.save()

                renderedImageL.pack()
                renderedImageR.pack()
                bpy.data.images.remove(renderedImage)
        else:
            if self.save_intermediate:
                pathMono = os.path.join(self.intermediate_path, face_name_base + self.tmpfext)
            else:
                pathMono = os.path.join(self.tmpdir, name + self.tmpfext)

            self.scene.render.filepath = pathMono
            bpy.ops.render.render(write_still=True)
            if not self.save_intermediate:
                self.createdFiles.add(pathMono)

            renderedImageL = bpy.data.images.load(pathMono)
            renderedImageL.name = name
            renderedImageR = None

        self.scene.render.filepath = org_filepath
        self.scene.render.image_settings.file_format = org_file_format
        self.scene.render.image_settings.color_depth = org_color_depth
        self.scene.render.image_settings.exr_codec = org_exr_codec
        return renderedImageL, renderedImageR


    def render_images(self):

        # update focus distance if focus object is set
        if self.camera.data.dof.use_dof and self.camera_origin.data.dof.focus_object is not None:
            focus_location = self.camera_origin.data.dof.focus_object.matrix_world.translation
            icm = self.camera_origin.matrix_world.inverted_safe()
            self.camera.data.dof.focus_distance = abs((icm @ focus_location).z)

        # Render the images for every direction
        image_list_l = []
        image_list_r = []

        directions = ['front']
        if not self.no_side_images:
            directions += ['left', 'right']
        if not self.no_top_bottom_images:
            directions += ['bottom', 'top']
        if not self.no_back_image:
            directions += ['back']

        self.direction_offsets = self.find_direction_offsets()
        for direction in reversed(directions):
            self.set_camera_direction(direction)
            imgl, imgr = self.render_image(direction)
            image_list_l.insert(0, imgl)
            image_list_r.insert(0, imgr)

        return image_list_l, image_list_r


    def render_and_save(self):

        frame_step = self.scene.frame_step

        # Render the images
        imageList, imageList2 = self.render_images()

        if self.skip_stitching:
            for img in imageList:
                if img: bpy.data.images.remove(img)
            if imageList2:
                for img in imageList2:
                    if img: bpy.data.images.remove(img)

            return

        # Determine final image path and name using Blender's frame_path
        final_image_path = self.scene.render.frame_path(frame=self.scene.frame_current)
        image_name = os.path.basename(final_image_path)

        start_time = time.time()
        # Convert to equirectangular and save
        if self.is_stereo:
            leftImage = self.cubemap_to_panorama(imageList, "Render Left")
            rightImage = self.cubemap_to_panorama(imageList2, "Render Right")

            if not image_name in bpy.data.images.keys():
                imageResult = bpy.data.images.new(image_name, leftImage.size[0], 2 * leftImage.size[1], float_buffer=self.is_float, alpha=self.has_alpha)

            imageResult = bpy.data.images[image_name]
            imageResult.file_format = self.fformat
            img1arr = np.empty((leftImage.size[1], 4 * leftImage.size[0]), dtype=np.float32)
            leftImage.pixels.foreach_get(img1arr.ravel())
            img2arr = np.empty((rightImage.size[1], 4 * rightImage.size[0]), dtype=np.float32)
            rightImage.pixels.foreach_get(img2arr.ravel())

            if self.sidebyside:
                imageResult.scale(2*leftImage.size[0], leftImage.size[1])
                if self.use_sidebyside_crosseyed:
                    imageResult.pixels.foreach_set(np.concatenate((img1arr, img2arr), axis=1).ravel())
                else:
                    imageResult.pixels.foreach_set(np.concatenate((img2arr, img1arr), axis=1).ravel())
            else:
                imageResult.scale(leftImage.size[0], 2*leftImage.size[1])
                if self.scene.eeVR.isTopRightEye:
                    imageResult.pixels.foreach_set(np.concatenate((img2arr, img1arr)).ravel())
                else:
                    imageResult.pixels.foreach_set(np.concatenate((img1arr, img2arr)).ravel())
            bpy.data.images.remove(leftImage)
            bpy.data.images.remove(rightImage)

        else:
            imageResult = self.cubemap_to_panorama(imageList, "RenderResult")

        save_start_time = time.time()

        # Ensure output directory exists
        os.makedirs(os.path.dirname(final_image_path), exist_ok=True)

        imageResult.update()
        imageResult.filepath_raw = final_image_path
        imageResult.save()

        print(f'''Saved '{imageResult.filepath_raw} float:{self.is_float} alpha:{self.has_alpha}'
 Time : {round(time.time() - start_time, 2)} seconds (Saving : {round(time.time() - save_start_time, 2)} seconds)
 ''')

        bpy.data.images.remove(imageResult)
