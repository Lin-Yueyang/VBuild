"""Illustration surfaces: camera-space highlights, texture and real silhouettes."""

import numpy as np
from vtkmodules.vtkRenderingCore import vtkActor, vtkGlyph3DMapper

SURFACES = {
    "illustration": """
        float tone = mix(0.44, 0.52 + 0.48*d, smoothstep(0.0, 0.28, abs(n.z)));
        fragOutput0.rgb = base * tone;
    """,
    "plastic": """
        vec3 body = base * (0.30 + 0.70*d);
        float oval = max(0.0, 1.0 - pow((n.x+0.34)/0.30, 2.0) - pow((n.y-0.43)/0.18, 2.0));
        float coat = pow(oval, 0.65);
        fragOutput0.rgb = mix(body, vec3(1.0), 0.92*coat);
    """,
    "rubber": """
        float grain = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898,78.233))) * 43758.5453);
        float tone = (0.55 + 0.45*d) * (0.91 + 0.12*grain);
        fragOutput0.rgb = base * tone;
    """,
    "toon": """
        float tone = d > 0.82 ? 1.0 : (d > 0.44 ? 0.79 : 0.57);
        fragOutput0.rgb = base * tone;
    """,
    "disc": "fragOutput0.rgb = base;",
    "flat": "fragOutput0.rgb = base;",
    "fruit": """
        vec2 tile = gl_FragCoord.xy/3.8;
        float seed = fract(sin(dot(floor(tile), vec2(12.9898,78.233))) * 43758.5453);
        float pore = (1.0-smoothstep(0.05, 0.22, length(fract(tile)-0.5))) * step(0.35, seed);
        float tone = 0.61 + 0.39*d;
        vec3 body = base * tone * (1.0-0.18*pore);
        float sheen = 0.38 * pow(d, 14.0);
        float rim = 0.10 * pow(1.0-abs(n.z), 2.0);
        fragOutput0.rgb = mix(body, vec3(1.0), sheen+rim);
    """,
}


def shading(actor, lighting):
    shader = actor.GetShaderProperty()
    shader.ClearFragmentShaderReplacement("//VTK::Light::Impl", True)
    if lighting in SURFACES:
        shader.AddFragmentShaderReplacement("//VTK::Light::Impl", True,
            "//VTK::Light::Impl\n"
            "vec3 n = normalize(normalVCVSOutput);\n"
            "vec3 base = ambientColor + diffuseColor;\n"
            "float d = max(0.0, dot(n, normalize(vec3(-0.35,0.45,1.0))));\n" + SURFACES[lighting], False)


def background_bonds(actor, enabled):
    shader = actor.GetShaderProperty()
    shader.ClearFragmentShaderReplacement("//VTK::Depth::Impl", True)
    if enabled:
        shader.AddFragmentShaderReplacement("//VTK::Depth::Impl", True,
            "//VTK::Depth::Impl\ngl_FragDepth = 0.999999;\n", False)


class Outlines:
    def __init__(self, renderer, data):
        self.renderer, self.data = renderer, data
        self.actors = {}

    def update(self, key, source, opacity, disc, sign):
        if key not in self.actors:
            mapper = vtkGlyph3DMapper()
            mapper.SetInputData(self.data)
            mapper.SetScaleArray("radius")
            mapper.SetScaleModeToScaleByMagnitude()
            mapper.SetScaleFactor(1.07)
            mapper.OrientOff()
            mapper.ScalarVisibilityOff()
            mapper.MaskingOn()
            mapper.SetMaskArray("element_" + key)
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(.10, .14, .18)
            actor.GetProperty().LightingOff()
            actor.PickableOff()
            actor.UseBoundsOff()
            self.renderer.AddActor(actor)
            self.actors[key] = actor
        actor = self.actors[key]
        actor.GetMapper().SetSourceConnection(source)
        prop = actor.GetProperty()
        prop.SetOpacity(opacity)
        prop.SetFrontfaceCulling(not disc and sign == 1)
        prop.SetBackfaceCulling(not disc and sign == -1)
        actor.SetPosition(*(np.array(self.renderer.GetActiveCamera().GetViewPlaneNormal()) * (-.005 if disc else 0)))
        actor.VisibilityOn()

    def hide(self):
        for actor in self.actors.values():
            actor.VisibilityOff()

    def sync(self, sign, disc):
        for actor in self.actors.values():
            prop = actor.GetProperty()
            prop.SetFrontfaceCulling(not disc and sign == 1)
            prop.SetBackfaceCulling(not disc and sign == -1)
